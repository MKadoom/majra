"""T7 — daily reconciliation: platform account totals vs stored facts."""
from datetime import date, timedelta

import pytest

from app.connectors.meta.connector import MetaConnector
from app.core.security import encrypt_tokens
from app.models import Account, Connection, FactAdPerformance, ReconciliationResult
from app.services import alerts
from app.services.reconciliation import mismatch_alert, reconcile, within
from app.workers import tasks

TODAY = date(2026, 10, 20)
D1, D2 = date(2026, 10, 12), date(2026, 10, 18)   # 7 days ending two days ago


@pytest.fixture
def owner(auth_client):
    return auth_client.get("/auth/me").json()["id"]


def _seed(db, uid, platform="meta", status="active", ext="act_1"):
    c = Connection(user_id=uid, platform=platform, name=platform, status=status,
                   encrypted_tokens=encrypt_tokens({"access_token": "t"}))
    db.add(c)
    db.flush()
    a = Account(connection_id=c.id, platform=platform, external_id=ext,
                name=f"{platform} {ext}", currency="SAR")
    db.add(a)
    db.flush()
    d = D1
    while d <= D2:
        db.add(FactAdPerformance(date=d, platform=platform, account_id=a.id,
                                 breakdown="none", spend=100, currency="SAR",
                                 impressions=1000, clicks=5, conversions=1,
                                 conversion_value=50, video_views=0, leads=0,
                                 purchases=2))
        d += timedelta(days=1)
    db.commit()
    return c, a


def _platform_totals(overrides=None):
    out, d = {}, D1
    while d <= D2:
        out[d.isoformat()] = {"spend": 100.0, "impressions": 1000, "purchases": 2}
        d += timedelta(days=1)
    for k, v in (overrides or {}).items():
        out[k].update(v)
    return out


def test_tolerance():
    assert within("spend", 100.0, 100.9)          # < 1 unit
    assert within("spend", 10_000, 10_090)        # < 1%
    assert not within("spend", 100.0, 102.5)
    assert within("purchases", 2, 3)              # 1 purchase
    assert not within("purchases", 10, 14)


def test_matching_data_is_all_ok(db, owner, monkeypatch):
    _seed(db, owner)
    monkeypatch.setattr(MetaConnector, "fetch_account_totals",
                        lambda self, *a: _platform_totals())
    s = reconcile(db, today=TODAY)
    assert (s["from"], s["to"]) == ("2026-10-12", "2026-10-18")
    assert s["checked"] == 7 * 3 and s["mismatches"] == [] and s["errors"] == []
    assert mismatch_alert(s) is None
    assert db.query(ReconciliationResult).filter_by(status="ok").count() == 21


def test_dropped_rows_are_caught(db, owner, monkeypatch):
    _seed(db, owner)
    monkeypatch.setattr(MetaConnector, "fetch_account_totals", lambda self, *a:
                        _platform_totals({"2026-10-15": {"spend": 180.0, "purchases": 5}}))
    s = reconcile(db, today=TODAY)
    got = {(m["date"], m["metric"]): (m["platform_value"], m["stored_value"])
           for m in s["mismatches"]}
    assert got == {("2026-10-15", "spend"): (180.0, 100.0),
                   ("2026-10-15", "purchases"): (5.0, 2.0)}
    alert = mismatch_alert(s)
    assert alert.kind == "data.mismatch"
    assert "2026-10-15 spend: platform 180.0 vs stored 100.0" in alert.text()


def test_a_day_missing_in_majra_is_a_mismatch(db, owner, monkeypatch):
    c, a = _seed(db, owner)
    db.query(FactAdPerformance).filter_by(date=date(2026, 10, 13)).delete()
    db.commit()
    monkeypatch.setattr(MetaConnector, "fetch_account_totals",
                        lambda self, *a: _platform_totals())
    s = reconcile(db, today=TODAY)
    assert {m["date"] for m in s["mismatches"]} == {"2026-10-13"}


def test_platform_errors_are_recorded_not_alerted(db, owner, monkeypatch):
    _seed(db, owner)

    def boom(self, *a):
        raise RuntimeError("[17] User request limit reached")
    monkeypatch.setattr(MetaConnector, "fetch_account_totals", boom)
    s = reconcile(db, today=TODAY)
    assert s["checked"] == 0 and mismatch_alert(s) is None
    assert "[17]" in s["errors"][0]["error"]
    assert db.query(ReconciliationResult).filter_by(status="error").count() == 1


def test_skips_unsupported_unhealthy_deleted_and_unlisted(db, owner, monkeypatch):
    from datetime import UTC, datetime

    calls = []
    monkeypatch.setattr(MetaConnector, "fetch_account_totals",
                        lambda self, tokens, ext, *a: calls.append(ext) or _platform_totals())
    _seed(db, owner, platform="tiktok", ext="tt")              # connector returns None
    _seed(db, owner, status="needs_reauth", ext="act_dead")
    dead, _ = _seed(db, owner, ext="act_gone")
    dead.deleted_at = datetime.now(UTC)
    listed, _ = _seed(db, owner, ext="act_keep")
    db.add(Account(connection_id=listed.id, platform="meta", external_id="act_other",
                   name="other", currency="SAR"))
    listed.synced_account_ids = ["act_keep"]
    db.commit()
    s = reconcile(db, today=TODAY)
    assert calls == ["act_keep"]
    assert s["mismatches"] == []


def test_api_latest_run_and_manual_run(auth_client, db, owner, monkeypatch):
    assert auth_client.get("/reconciliation").json()["run_at"] is None
    _seed(db, owner)
    monkeypatch.setattr(MetaConnector, "fetch_account_totals", lambda self, *a:
                        _platform_totals({"2026-10-16": {"impressions": 5000}}))
    monkeypatch.setattr("app.services.reconciliation.date",
                        type("D", (date,), {"today": staticmethod(lambda: TODAY)}))
    r = auth_client.post("/reconciliation/run")
    assert r.status_code == 200 and len(r.json()["mismatches"]) == 1
    body = auth_client.get("/reconciliation").json()
    assert body["checked"] == 21
    assert body["mismatches"][0]["metric"] == "impressions"
    assert body["mismatches"][0]["date"] == "2026-10-16"


def test_reconcile_task_alerts(db, owner, monkeypatch):
    _seed(db, owner)
    sent = []
    monkeypatch.setattr(alerts, "send", lambda a: sent.append(a) or True)
    monkeypatch.setattr(MetaConnector, "fetch_account_totals", lambda self, *a:
                        _platform_totals({"2026-10-12": {"spend": 0.0}}))
    monkeypatch.setattr("app.services.reconciliation.date",
                        type("D", (date,), {"today": staticmethod(lambda: TODAY)}))
    assert tasks.reconcile_task() == 1
    assert sent[0].kind == "data.mismatch"
    assert tasks.celery_app.conf.beat_schedule["reconcile"]["task"].endswith("reconcile_task")
