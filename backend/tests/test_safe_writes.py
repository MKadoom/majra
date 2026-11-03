"""T4 — a safe write path.

- Syncs of one connection are serialized by a transaction-scoped advisory lock,
  so overlapping syncs can't double-count.
- An empty platform answer never wipes rows we already hold.
- Watermarks are per account (app.account_sync_state); a lagging account pulls
  the next range back, in month-sized chunks, never past the backfill horizon.
- Backfill completion is recorded.
"""
import threading
import time
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select, text

from app.connectors.base import TokenSet, TransientError
from app.core.db import engine
from app.core.security import encrypt_tokens
from app.models import (
    Account,
    AccountSyncState,
    Connection,
    FactAdPerformance,
    FactWebPerformance,
    Schedule,
    SyncJob,
)
from app.services import sync as sync_mod
from app.services.schedules import tick_schedules
from app.services.sync import (
    SYNC_LOCK_CLASS,
    account_watermark,
    advance_coverage,
    incremental_range,
    run_sync,
    split_range,
)
from tests.test_sync_and_metrics import GRAPH, _insight, _mock_graph


@pytest.fixture
def conn(auth_client, db):
    uid = auth_client.get("/auth/me").json()["id"]
    c = Connection(user_id=uid, platform="meta", name="Meta",
                   encrypted_tokens=encrypt_tokens({"access_token": "t"}))
    db.add(c)
    db.commit()
    return c


def _job(db, conn, d1="2026-06-01", d2="2026-06-02", kind="manual"):
    j = SyncJob(connection_id=conn.id, date_from=date.fromisoformat(d1),
                date_to=date.fromisoformat(d2), breakdown="none", kind=kind)
    db.add(j)
    db.commit()
    return j


def _facts(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(FactAdPerformance))


def _hold_lock(connection_id):
    """Take the sync lock for `connection_id` from another DB session."""
    c = engine.connect()
    tx = c.begin()
    c.execute(text("SELECT pg_advisory_xact_lock(:a, :b)"),
              {"a": SYNC_LOCK_CLASS, "b": connection_id})
    return c, tx


# ------------------------------------------------------------------ locking
@respx.mock
def test_sync_gives_up_with_retryable_error_while_another_holds_the_lock(
        db, conn, monkeypatch):
    _mock_graph()
    monkeypatch.setattr(sync_mod, "SYNC_LOCK_TIMEOUT", "300ms")
    c, tx = _hold_lock(conn.id)
    try:
        job = _job(db, conn)
        with pytest.raises(TransientError, match="busy"):
            run_sync(db, job.id)
        assert _facts(db) == 0
    finally:
        tx.rollback()
        c.close()
    # lock released → the retry goes through
    job2 = _job(db, conn)
    assert run_sync(db, job2.id).status == "success"
    assert _facts(db) == 4


@respx.mock
def test_overlapping_sync_waits_then_replaces_instead_of_doubling(db, conn):
    """Sync A commits 4 rows; sync B was blocked on the lock meanwhile, and when
    it proceeds it deletes A's rows before inserting — still 4, not 8."""
    from app.core.db import SessionLocal

    _mock_graph()
    first = _job(db, conn)
    assert run_sync(db, first.id).status == "success"
    assert _facts(db) == 4

    c, tx = _hold_lock(conn.id)          # pretend a sync is mid-write
    second = _job(db, conn)
    result: dict = {}

    def worker():
        s = SessionLocal()
        try:
            t0 = time.monotonic()
            result["status"] = run_sync(s, second.id).status
            result["waited"] = time.monotonic() - t0
        finally:
            s.close()

    th = threading.Thread(target=worker)
    th.start()
    time.sleep(0.6)
    assert th.is_alive()                 # blocked on the lock, not writing
    tx.rollback()
    c.close()
    th.join(timeout=30)
    assert result["status"] == "success"
    assert result["waited"] >= 0.5
    assert _facts(db) == 4


# ------------------------------------------------------------ empty fetch
@respx.mock
def test_empty_platform_answer_keeps_stored_rows(db, conn):
    _mock_graph()
    assert run_sync(db, _job(db, conn).id).status == "success"
    assert _facts(db) == 4

    respx.get(f"{GRAPH}/act_1/insights").mock(
        return_value=httpx.Response(200, json={"data": []}))
    job = run_sync(db, _job(db, conn).id)
    assert job.status == "success"
    assert job.stats["accounts_empty_kept"] == ["act_1"]
    assert job.stats["rows_deleted"] == 0
    assert _facts(db) == 4


@respx.mock
def test_empty_answer_with_nothing_stored_is_just_empty(db, conn):
    _mock_graph()
    respx.get(f"{GRAPH}/act_1/insights").mock(
        return_value=httpx.Response(200, json={"data": []}))
    job = run_sync(db, _job(db, conn).id)
    assert job.stats["accounts_empty_kept"] == []
    assert job.stats["accounts"] == 1


@respx.mock
def test_non_empty_answer_still_replaces(db, conn):
    _mock_graph()
    run_sync(db, _job(db, conn).id)
    respx.get(f"{GRAPH}/act_1/insights").mock(return_value=httpx.Response(
        200, json={"data": [_insight("2026-06-01", "a1", "99")]}))
    job = run_sync(db, _job(db, conn).id)
    assert job.stats["rows_deleted"] == 4 and job.stats["rows_inserted"] == 1
    assert _facts(db) == 1


def test_web_handler_keeps_rows_on_empty_answer(db, conn):
    acc = Account(connection_id=conn.id, platform="ga4", external_id="p1", name="P")
    db.add(acc)
    db.flush()
    db.add(FactWebPerformance(date=date(2026, 6, 1), account_id=acc.id,
                              sessions=5, users=4, conversions=0, revenue=0))
    job = _job(db, conn, "2026-06-01", "2026-06-01")

    class Empty:
        raw_model = None

        def fetch_insights(self, *a):
            return iter(())

    stats = sync_mod._handle_web_performance(db, conn, Empty(), job, [acc],
                                             TokenSet("t"))
    assert stats["accounts_empty_kept"] == ["p1"] and stats["rows_deleted"] == 0
    assert db.scalar(select(func.count()).select_from(FactWebPerformance)) == 1


# ------------------------------------------------------------ coverage
def test_coverage_extends_only_when_contiguous(db, conn):
    acc = Account(connection_id=conn.id, platform="meta", external_id="x", name="X")
    db.add(acc)
    db.flush()
    advance_coverage(db, acc.id, "none", date(2026, 6, 1), date(2026, 6, 2))
    advance_coverage(db, acc.id, "none", date(2026, 6, 3), date(2026, 6, 5))  # adjacent
    advance_coverage(db, acc.id, "none", date(2026, 5, 20), date(2026, 6, 1))  # overlap
    advance_coverage(db, acc.id, "none", date(2026, 6, 20), date(2026, 6, 21))  # gap
    db.flush()
    st = db.get(AccountSyncState, (acc.id, "none"))
    assert (st.synced_from, st.synced_to) == (date(2026, 5, 20), date(2026, 6, 5))


@respx.mock
def test_successful_sync_records_coverage_but_skipped_account_does_not(db, conn):
    respx.get(f"{GRAPH}/me/adaccounts").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "act_ok", "name": "OK", "currency": "SAR"},
                 {"id": "act_bad", "name": "Bad", "currency": "SAR"}]}))
    respx.get(url__regex=rf"{GRAPH}/act_ok/insights.*").mock(
        return_value=httpx.Response(200, json={"data": []}))
    respx.get(url__regex=rf"{GRAPH}/act_bad/.*").mock(return_value=httpx.Response(
        400, json={"error": {"code": 17, "message": "User request limit reached"}}))
    run_sync(db, _job(db, conn).id)
    states = {db.get(Account, s.account_id).external_id: s
              for s in db.scalars(select(AccountSyncState))}
    assert set(states) == {"act_ok"}  # an empty-but-OK answer still counts
    assert states["act_ok"].synced_to == date(2026, 6, 2)


def test_watermark_is_the_laggiest_account(db, conn):
    a = Account(connection_id=conn.id, platform="meta", external_id="a", name="A")
    b = Account(connection_id=conn.id, platform="meta", external_id="b", name="B")
    db.add_all([a, b])
    db.flush()
    db.add_all([
        AccountSyncState(account_id=a.id, breakdown="none",
                         synced_from=date(2026, 1, 1), synced_to=date(2026, 6, 20)),
        AccountSyncState(account_id=b.id, breakdown="none",
                         synced_from=date(2026, 1, 1), synced_to=date(2026, 6, 10)),
    ])
    db.commit()
    assert account_watermark(db, conn.id, "none") == date(2026, 6, 10)
    rng = incremental_range(db, conn.id, "none", 7, today=date(2026, 6, 25))
    assert rng == (date(2026, 6, 3), date(2026, 6, 24))
    # an allow-list narrows which accounts count
    conn.synced_account_ids = ["a"]
    db.commit()
    assert account_watermark(db, conn.id, "none") == date(2026, 6, 20)


def test_watermark_falls_back_to_facts_without_coverage(db, conn):
    acc = Account(connection_id=conn.id, platform="meta", external_id="a", name="A")
    db.add(acc)
    db.flush()
    db.add(FactAdPerformance(date=date(2026, 6, 4), platform="meta", account_id=acc.id,
                             breakdown="none", spend=1, currency="SAR", impressions=1,
                             clicks=0, conversions=0, conversion_value=0,
                             video_views=0, leads=0, purchases=0))
    db.commit()
    assert account_watermark(db, conn.id, "none") is None
    assert incremental_range(db, conn.id, "none", 2, today=date(2026, 6, 10)) == (
        date(2026, 6, 2), date(2026, 6, 9))


def test_split_range():
    assert split_range(date(2026, 1, 1), date(2026, 1, 1)) == [
        (date(2026, 1, 1), date(2026, 1, 1))]
    chunks = split_range(date(2026, 1, 1), date(2026, 3, 15))
    assert chunks[0] == (date(2026, 1, 1), date(2026, 1, 30))
    assert chunks[-1][1] == date(2026, 3, 15)
    assert all((e - s).days < 30 for s, e in chunks)


def test_tick_chunks_catch_up_and_stops_at_horizon(db, conn):
    a = Account(connection_id=conn.id, platform="meta", external_id="a", name="A")
    db.add(a)
    db.flush()
    db.add(AccountSyncState(account_id=a.id, breakdown="none",
                            synced_from=date(2025, 1, 1), synced_to=date(2026, 3, 1)))
    db.add(Schedule(connection_id=conn.id, cadence="daily", hour_utc=0,
                    breakdowns=["none"], restatement_days=7, backfill_days=60))
    db.commit()
    now = datetime(2026, 6, 10, 0, 5, tzinfo=UTC)
    tick_schedules(db, now=now)
    jobs = db.scalars(select(SyncJob).order_by(SyncJob.date_from)).all()
    yesterday = date(2026, 6, 9)
    assert jobs[0].date_from == yesterday - timedelta(days=60)   # capped at horizon
    assert jobs[-1].date_to == yesterday
    assert all((j.date_to - j.date_from).days < 30 for j in jobs)
    assert all(j.kind == "scheduled" for j in jobs)


def test_tick_records_backfill_completion(db, conn):
    a = Account(connection_id=conn.id, platform="meta", external_id="a", name="A")
    db.add(a)
    db.flush()
    db.add(AccountSyncState(account_id=a.id, breakdown="none",
                            synced_from=date(2026, 1, 1), synced_to=date(2026, 6, 8)))
    db.add(Schedule(connection_id=conn.id, cadence="daily", hour_utc=0,
                    breakdowns=["none"], backfill_days=30))
    db.commit()
    tick_schedules(db, now=datetime(2026, 6, 10, 0, 5, tzinfo=UTC))
    db.expire_all()
    assert db.get(AccountSyncState, (a.id, "none")).backfill_completed_at is not None


def test_backfill_not_marked_complete_when_coverage_is_short(db, conn):
    a = Account(connection_id=conn.id, platform="meta", external_id="a", name="A")
    db.add(a)
    db.flush()
    db.add(AccountSyncState(account_id=a.id, breakdown="none",
                            synced_from=date(2026, 6, 1), synced_to=date(2026, 6, 8)))
    db.add(Schedule(connection_id=conn.id, cadence="daily", hour_utc=0,
                    breakdowns=["none"], backfill_days=365))
    db.commit()
    tick_schedules(db, now=datetime(2026, 6, 10, 0, 5, tzinfo=UTC))
    db.expire_all()
    assert db.get(AccountSyncState, (a.id, "none")).backfill_completed_at is None
