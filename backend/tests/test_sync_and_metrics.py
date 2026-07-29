"""End-to-end: mocked Meta Graph API → sync → warehouse → /metrics.

Proves: raw storage, dim upserts, pagination handling, idempotent re-sync
(delete+insert), watermark logic, blended metrics query, schedule ticking.
"""
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.security import encrypt_tokens
from app.models import (
    Account,
    Connection,
    FactAdPerformance,
    RawMetaInsights,
    Schedule,
    SyncJob,
)
from app.services.schedules import tick_schedules
from app.services.sync import incremental_range, watermark

GRAPH = "https://graph.facebook.com/v21.0"


def _insight(day: str, ad: str, spend: str, purchases: str = "1"):
    return {
        "date_start": day,
        "campaign_id": "c1", "campaign_name": "Camp One",
        "adset_id": "s1", "adset_name": "Set One",
        "ad_id": ad, "ad_name": f"Ad {ad}",
        "spend": spend, "impressions": "1000", "clicks": "50",
        "actions": [{"action_type": "omni_purchase", "value": purchases}],
        "action_values": [{"action_type": "omni_purchase", "value": "100.0"}],
    }


PAGE2_URL = f"{GRAPH}/act_1/insights_next_page"


def _mock_graph():
    respx.get(f"{GRAPH}/me/adaccounts").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": "act_1", "name": "Main SAR", "currency": "SAR",
                            "timezone_name": "Asia/Riyadh"}]},
        )
    )
    respx.get(f"{GRAPH}/act_1/campaigns").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "c1", "name": "Camp One",
                                 "status": "ACTIVE", "objective": "OUTCOME_SALES"}]}
        )
    )
    respx.get(f"{GRAPH}/act_1/adsets").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "s1", "name": "Set One",
                                 "status": "ACTIVE", "campaign_id": "c1"}]}
        )
    )
    respx.get(f"{GRAPH}/act_1/ads").mock(
        return_value=httpx.Response(
            200,
            json={"data": [
                {"id": "a1", "name": "Ad a1", "status": "ACTIVE", "adset_id": "s1"},
                {"id": "a2", "name": "Ad a2", "status": "ACTIVE", "adset_id": "s1"},
            ]},
        )
    )
    # insights: page 1 (2 rows) → paging.next → page 2 (2 rows)
    respx.get(f"{GRAPH}/act_1/insights").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [_insight("2026-06-01", "a1", "10.5"),
                         _insight("2026-06-01", "a2", "4.5")],
                "paging": {"next": PAGE2_URL},
            },
        )
    )
    respx.get(PAGE2_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": [_insight("2026-06-02", "a1", "3.0"),
                           _insight("2026-06-02", "a2", "2.0")]},
        )
    )


@pytest.fixture
def connection(auth_client, db):
    user_id = auth_client.get("/auth/me").json()["id"]
    conn = Connection(
        user_id=user_id, platform="meta", name="test meta",
        encrypted_tokens=encrypt_tokens({"access_token": "fake-token"}),
    )
    db.add(conn)
    db.commit()
    return conn


@respx.mock
def test_full_sync_idempotent_and_metrics(auth_client, db, connection):
    _mock_graph()
    body = {
        "connection_id": connection.id,
        "date_from": "2026-06-01",
        "date_to": "2026-06-02",
        "breakdown": "none",
    }

    # --- first sync
    r = auth_client.post("/syncs", json=body)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "success", job["error"]
    assert job["stats"]["rows_inserted"] == 4
    assert job["stats"]["rows_deleted"] == 0

    facts = db.scalar(select(func.count()).select_from(FactAdPerformance))
    raw = db.scalar(select(func.count()).select_from(RawMetaInsights))
    assert facts == 4 and raw == 4

    # currency travels with every row (SPEC §3)
    assert db.scalars(select(FactAdPerformance.currency).distinct()).all() == ["SAR"]

    # --- second sync of the same range: idempotent (delete+insert)
    r2 = auth_client.post("/syncs", json=body)
    assert r2.json()["stats"]["rows_deleted"] == 4
    facts2 = db.scalar(select(func.count()).select_from(FactAdPerformance))
    assert facts2 == 4  # unchanged
    raw2 = db.scalar(select(func.count()).select_from(RawMetaInsights))
    assert raw2 == 8  # raw layer is append-only

    # --- watermark
    assert watermark(db, connection.id, "none") == date(2026, 6, 2)
    rng = incremental_range(db, connection.id, "none", restatement_days=7)
    assert rng is not None and rng[0] == date(2026, 6, 2) - timedelta(days=7)

    # --- metrics: totals
    r = auth_client.get(
        "/metrics",
        params={"date_from": "2026-06-01", "date_to": "2026-06-02"},
    )
    assert r.status_code == 200, r.text
    row = r.json()["data"][0]
    assert row["spend"] == 20.0
    assert row["impressions"] == 4000 and row["clicks"] == 200
    assert row["conversions"] == 4.0 and row["conversion_value"] == 400.0
    assert row["ctr"] == 5.0          # 200/4000*100
    assert row["cpc"] == 0.1          # 20/200
    assert row["roas"] == 20.0        # 400/20

    # --- metrics: grouped by platform+date, sorted
    r = auth_client.get(
        "/metrics",
        params={
            "date_from": "2026-06-01", "date_to": "2026-06-02",
            "group_by": ["platform", "date"], "sort": "date",
        },
    )
    data = r.json()["data"]
    assert [d["date"] for d in data] == ["2026-06-01", "2026-06-02"]
    assert data[0]["platform"] == "meta" and data[0]["spend"] == 15.0
    assert data[1]["spend"] == 5.0
    assert r.json()["total"] == 2

    # --- metrics: campaign grouping joins names in
    r = auth_client.get(
        "/metrics",
        params={"date_from": "2026-06-01", "date_to": "2026-06-02",
                "group_by": ["campaign"]},
    )
    assert r.json()["data"][0]["campaign"] == "Camp One"

    # --- logs endpoint has structured events
    logs = auth_client.get("/logs", params={"sync_job_id": job["id"]}).json()
    events = [entry["event"] for entry in logs]
    assert "accounts.synced" in events and "sync.finished" in events


@respx.mock
def test_permanent_error_fails_job(auth_client, db, connection):
    respx.get(f"{GRAPH}/me/adaccounts").mock(
        return_value=httpx.Response(
            400, json={"error": {"code": 190, "message": "token expired"}}
        )
    )
    r = auth_client.post(
        "/syncs",
        json={"connection_id": connection.id, "date_from": "2026-06-01",
              "date_to": "2026-06-01"},
    )
    job = r.json()
    assert job["status"] == "failed"
    assert "190" in job["error"]


def test_schedule_tick_creates_backfill_then_incremental(db, connection):
    s = Schedule(
        connection_id=connection.id, cadence="daily", hour_utc=3,
        breakdowns=["none"], restatement_days=7, backfill_days=60,
    )
    db.add(s)
    db.commit()

    now = datetime(2026, 7, 7, 3, 10, tzinfo=UTC)

    # no data yet → backfill chunks (60 days ≈ 2-3 month chunks)
    created = tick_schedules(db, now=now)
    jobs = db.scalars(select(SyncJob).where(SyncJob.id.in_(created))).all()
    assert len(jobs) >= 2 and all(j.kind == "backfill" for j in jobs)
    assert min(j.date_from for j in jobs) == date(2026, 7, 6) - timedelta(days=60)
    assert max(j.date_to for j in jobs) == date(2026, 7, 6)

    # not due again 1 hour later same day
    assert tick_schedules(db, now=now + timedelta(hours=1)) == []

    # simulate synced data → next day's tick is a single incremental job
    acc = Account(connection_id=connection.id, platform="meta",
                  external_id="act_1", name="Main")
    db.add(acc)
    db.flush()
    db.add(FactAdPerformance(
        date=date(2026, 7, 6), platform="meta", account_id=acc.id,
        breakdown="none", spend=1, impressions=1, clicks=1,
    ))
    db.commit()

    nxt = now + timedelta(days=1)
    created2 = tick_schedules(db, now=nxt)
    assert len(created2) == 1
    j = db.get(SyncJob, created2[0])
    assert j.kind == "scheduled"
    assert j.date_from == date(2026, 7, 6) - timedelta(days=7)  # restatement window
    assert j.date_to == date(2026, 7, 7)


def _mock_graph_two_accounts():
    """Two ad accounts (act_1, act_2), each with one campaign/adset/ad and one
    insight row — for exercising the per-connection account allow-list."""
    respx.get(f"{GRAPH}/me/adaccounts").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "act_1", "name": "Main SAR", "currency": "SAR",
             "timezone_name": "Asia/Riyadh"},
            {"id": "act_2", "name": "Other USD", "currency": "USD",
             "timezone_name": "Asia/Bahrain"},
        ]})
    )
    for acc in ("act_1", "act_2"):
        respx.get(f"{GRAPH}/{acc}/campaigns").mock(return_value=httpx.Response(
            200, json={"data": [{"id": f"c_{acc}", "name": "Camp",
                                 "status": "ACTIVE", "objective": "OUTCOME_SALES"}]}))
        respx.get(f"{GRAPH}/{acc}/adsets").mock(return_value=httpx.Response(
            200, json={"data": [{"id": f"s_{acc}", "name": "Set",
                                 "status": "ACTIVE", "campaign_id": f"c_{acc}"}]}))
        respx.get(f"{GRAPH}/{acc}/ads").mock(return_value=httpx.Response(
            200, json={"data": [{"id": f"a_{acc}", "name": "Ad",
                                 "status": "ACTIVE", "adset_id": f"s_{acc}"}]}))
        respx.get(f"{GRAPH}/{acc}/insights").mock(return_value=httpx.Response(
            200, json={"data": [{
                "date_start": "2026-06-01",
                "campaign_id": f"c_{acc}", "campaign_name": "Camp",
                "adset_id": f"s_{acc}", "adset_name": "Set",
                "ad_id": f"a_{acc}", "ad_name": "Ad",
                "spend": "10.0", "impressions": "100", "clicks": "5",
                "actions": [{"action_type": "omni_purchase", "value": "1"}],
                "action_values": [{"action_type": "omni_purchase", "value": "50"}],
            }]}))


@respx.mock
def test_synced_account_allowlist_restricts_sync(auth_client, db, connection):
    """A connection can be pinned to a subset of its accounts; only those sync."""
    _mock_graph_two_accounts()

    r = auth_client.patch(
        f"/connections/{connection.id}/synced-accounts",
        json={"account_external_ids": ["act_2"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["synced_account_ids"] == ["act_2"]

    body = {"connection_id": connection.id, "date_from": "2026-06-01",
            "date_to": "2026-06-01", "breakdown": "none"}
    job = auth_client.post("/syncs", json=body).json()
    assert job["status"] == "success", job.get("error")
    assert job["stats"]["accounts"] == 1  # only the allow-listed account synced

    facts = db.scalars(select(FactAdPerformance)).all()
    assert len(facts) == 1
    act2 = db.scalar(select(Account).where(Account.external_id == "act_2"))
    assert {f.account_id for f in facts} == {act2.id}

    # clearing the allow-list restores "sync all" (stored as NULL)
    r2 = auth_client.patch(
        f"/connections/{connection.id}/synced-accounts",
        json={"account_external_ids": []},
    )
    assert r2.json()["synced_account_ids"] is None


@respx.mock
def test_metrics_currency_normalization(auth_client, db, connection):
    """spend/value from mixed-currency accounts blend into a reporting currency."""
    _mock_graph_two_accounts()  # act_1 SAR spend 10, act_2 USD spend 10
    body = {"connection_id": connection.id, "date_from": "2026-06-01",
            "date_to": "2026-06-01", "breakdown": "none"}
    assert auth_client.post("/syncs", json=body).json()["status"] == "success"

    # 1 SAR = 0.25 USD
    assert auth_client.put(
        "/fx-rates", json={"currency": "SAR", "rate_to_usd": 0.25}
    ).status_code == 200

    # raw (mixed currencies, not meaningful but unchanged): 10 + 10 = 20
    raw = auth_client.get("/metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-01"}).json()
    assert raw["data"][0]["spend"] == 20.0 and raw["currency"] is None

    # blended into USD: 10 SAR -> 2.5 + 10 USD -> 10 = 12.5
    usd = auth_client.get("/metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-01", "currency": "USD"}).json()
    assert usd["currency"] == "USD"
    assert usd["data"][0]["spend"] == 12.5
