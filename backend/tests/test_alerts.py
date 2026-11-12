"""T2 — alerts: channels, alert builders, and every worker task that raises one."""
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from app.core import config
from app.core.security import encrypt_tokens
from app.models import Account, Budget, Connection, FactAdPerformance, SyncJob
from app.services import alerts
from app.workers import tasks

WA_URL = "https://graph.facebook.com/v26.0/PNID/messages"


@pytest.fixture
def sent(monkeypatch):
    """Capture alerts instead of delivering them."""
    box: list[alerts.Alert] = []
    monkeypatch.setattr(alerts, "send", lambda a: box.append(a) or True)
    return box


@pytest.fixture
def whatsapp(monkeypatch):
    s = alerts.alert_settings
    monkeypatch.setattr(s, "alert_whatsapp_token", "wa-secret-token")
    monkeypatch.setattr(s, "alert_whatsapp_phone_number_id", "PNID")
    monkeypatch.setattr(s, "alert_whatsapp_to", "+966500000001, 966500000002")
    monkeypatch.setattr(s, "alert_whatsapp_template", "")
    return s


def _conn(db, user_id=None, platform="meta", name="Main", tokens=None):
    from app.core.security import hash_password
    from app.models import User

    if user_id is None:
        u = User(email=f"{name.lower()}@x.co", password_hash=hash_password("p"))
        db.add(u)
        db.flush()
        user_id = u.id
    c = Connection(user_id=user_id, platform=platform, name=name,
                   encrypted_tokens=encrypt_tokens(tokens or {"access_token": "t"}))
    db.add(c)
    db.commit()
    return c


def _fact(acc, day, spend, currency="SAR"):
    return FactAdPerformance(
        date=day, platform=acc.platform, account_id=acc.id, breakdown="none",
        spend=spend, currency=currency, impressions=1, clicks=0, conversions=0,
        conversion_value=0, video_views=0, leads=0, purchases=0)


# ------------------------------------------------------------------ channels
def test_no_channel_configured_by_default(client):
    assert alerts.alerts_configured() is False
    body = client.get("/health").json()
    assert body["alerts_configured"] is False
    assert body["alert_channels"] == []


def test_health_reports_channels_not_secrets(client, whatsapp):
    body = client.get("/health").json()
    assert body["alerts_configured"] is True
    assert body["alert_channels"] == ["whatsapp"]
    assert "wa-secret-token" not in str(body)


@respx.mock
def test_whatsapp_text_message_to_each_recipient(whatsapp):
    route = respx.post(WA_URL).mock(return_value=httpx.Response(200, json={}))
    ok = alerts.send(alerts.Alert(kind="sync.failed", title="sync failed",
                                  lines=["meta · Main", "error: boom"]))
    assert ok is True
    assert route.call_count == 2
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer wa-secret-token"
    body = httpx.Request("POST", "x", content=req.content).read()
    import json
    payload = json.loads(body)
    assert payload["to"] == "966500000001"
    assert payload["type"] == "text"
    assert "sync failed" in payload["text"]["body"]
    assert "error: boom" in payload["text"]["body"]


@respx.mock
def test_whatsapp_template_flattens_text(whatsapp, monkeypatch):
    import json

    monkeypatch.setattr(whatsapp, "alert_whatsapp_template", "majra_alert")
    monkeypatch.setattr(whatsapp, "alert_whatsapp_to", "966500000001")
    route = respx.post(WA_URL).mock(return_value=httpx.Response(200, json={}))
    alerts.send(alerts.Alert(kind="k", title="t", lines=["a\nb", "c    d"]))
    payload = json.loads(route.calls[0].request.content)
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "majra_alert"
    text = payload["template"]["components"][0]["parameters"][0]["text"]
    assert "\n" not in text and "    " not in text
    assert text == "t · a b · c d"


@respx.mock
def test_whatsapp_failure_never_raises(whatsapp):
    respx.post(WA_URL).mock(return_value=httpx.Response(500))
    assert alerts.send(alerts.Alert(kind="k", title="t")) is False
    respx.post(WA_URL).mock(side_effect=httpx.ConnectError("down"))
    assert alerts.send(alerts.Alert(kind="k", title="t")) is False


@respx.mock
def test_webhook_channel_keeps_event_payload(monkeypatch):
    import json

    monkeypatch.setattr(config.settings, "alert_webhook_url", "https://hook.test/x")
    route = respx.post("https://hook.test/x").mock(return_value=httpx.Response(204))
    assert alerts.send(alerts.Alert(kind="data.stale", title="t",
                                    payload={"connections": [1]}))
    assert json.loads(route.calls[0].request.content) == {
        "event": "data.stale", "connections": [1]}
    respx.post("https://hook.test/x").mock(side_effect=httpx.ConnectError("x"))
    assert alerts.send(alerts.Alert(kind="k", title="t")) is False


def test_send_helper_swallows_channel_errors(monkeypatch):
    def boom(a):
        raise RuntimeError("x")
    monkeypatch.setattr(alerts, "send", boom)
    tasks._send(alerts.Alert(kind="k", title="t"))  # must not raise
    tasks._send(None)


# ------------------------------------------------------- sync-related alerts
def test_final_sync_failure_alerts(db, sent):
    c = _conn(db)
    job = SyncJob(connection_id=c.id, date_from=date(2026, 6, 1),
                  date_to=date(2026, 6, 2), status="failed")
    db.add(job)
    db.commit()
    tasks.SyncTask().on_failure(RuntimeError("boom"), "tid", (job.id,), {}, None)
    assert [a.kind for a in sent] == ["sync.failed"]
    text = sent[0].text()
    assert "meta · Main" in text and "boom" in text and "2026-06-01" in text


def test_failure_alert_without_a_job(db, sent):
    tasks.SyncTask().on_failure(RuntimeError("x"), "tid", (), {"sync_job_id": None}, None)
    assert "unknown connection" in sent[0].text()


def test_skipped_accounts_alert_after_successful_sync(db, sent, monkeypatch):
    c = _conn(db)
    job = SyncJob(connection_id=c.id, date_from=date(2026, 6, 1),
                  date_to=date(2026, 6, 1), status="success",
                  stats={"accounts": 1, "accounts_skipped": 1,
                         "accounts_skipped_ids": ["act_1217959466650120"]})
    db.add(job)
    db.commit()
    monkeypatch.setattr(tasks, "run_sync", lambda db, jid: db.get(SyncJob, jid))
    tasks.run_sync_task(job.id)
    assert [a.kind for a in sent] == ["accounts.skipped"]
    assert "act_1217959466650120" in sent[0].text()


def test_clean_sync_sends_nothing(db, sent, monkeypatch):
    c = _conn(db)
    job = SyncJob(connection_id=c.id, date_from=date(2026, 6, 1),
                  date_to=date(2026, 6, 1), status="success",
                  stats={"accounts": 2, "accounts_skipped": 0})
    db.add(job)
    db.commit()
    monkeypatch.setattr(tasks, "run_sync", lambda db, jid: db.get(SyncJob, jid))
    tasks.run_sync_task(job.id)
    assert sent == []


def test_api_warning_header_alerts(db, sent, monkeypatch):
    c = _conn(db)
    job = SyncJob(connection_id=c.id, date_from=date(2026, 6, 1),
                  date_to=date(2026, 6, 1), status="success",
                  stats={"accounts": 1, "accounts_skipped": 0,
                         "api_warnings": ["x-ad-api-version-warning: v21.0 retires"]})
    db.add(job)
    db.commit()
    monkeypatch.setattr(tasks, "run_sync", lambda db, jid: db.get(SyncJob, jid))
    tasks.run_sync_task(job.id)
    assert [a.kind for a in sent] == ["api.version"]
    assert "v21.0 retires" in sent[0].text()


def test_meta_connector_collects_version_warning_headers():
    from app.connectors.meta.connector import MetaConnector

    m = MetaConnector()
    m._note_version_warning(httpx.Response(200, headers={"x-other": "1"}))
    assert not getattr(m, "api_warnings", None)
    m._note_version_warning(httpx.Response(
        200, headers={"X-Ad-Api-Version-Warning": "The call is using v21.0"}))
    assert m.api_warnings == {"x-ad-api-version-warning: The call is using v21.0"}


def test_sync_records_skipped_ids_and_api_warnings(auth_client, db):
    """End-to-end through the real sync engine: one account errors, the other
    syncs, and Meta sends a version-warning header."""
    from tests.test_sync_and_metrics import GRAPH

    user_id = auth_client.get("/auth/me").json()["id"]
    c = _conn(db, user_id=user_id)
    warn = {"x-ad-api-version-warning": "v21.0 will be retired"}
    with respx.mock:
        respx.get(f"{GRAPH}/me/adaccounts").mock(return_value=httpx.Response(
            200, headers=warn, json={"data": [
                {"id": "act_ok", "name": "OK", "currency": "SAR"},
                {"id": "act_bad", "name": "Bad", "currency": "SAR"}]}))
        respx.get(url__regex=rf"{GRAPH}/act_ok/insights.*").mock(
            return_value=httpx.Response(200, json={"data": []}))
        respx.get(url__regex=rf"{GRAPH}/act_bad/.*").mock(
            return_value=httpx.Response(
                400, json={"error": {"code": 100, "message": "bad account"}}))
        r = auth_client.post("/syncs", json={
            "connection_id": c.id, "date_from": "2026-06-01", "date_to": "2026-06-01"})
    stats = r.json()["stats"]
    assert stats["accounts_skipped"] == 1
    assert stats["accounts_skipped_ids"] == ["act_bad"]
    assert stats["api_warnings"] == ["x-ad-api-version-warning: v21.0 will be retired"]


# ------------------------------------------------------------- token expiry
def test_expiring_tokens_window(db):
    now = datetime(2026, 12, 12, tzinfo=UTC)
    soon = _conn(db, name="Soon", tokens={
        "access_token": "a", "expires_at": "2026-12-26T00:00:00+00:00"})
    _conn(db, name="Later", tokens={
        "access_token": "a", "expires_at": "2027-02-01T00:00:00Z"})
    gone = _conn(db, name="Gone", tokens={
        "access_token": "a", "expires_at": "2026-12-01T00:00:00"})
    _conn(db, name="Forever", tokens={"access_token": "a"})
    _conn(db, name="Junk", tokens={"access_token": "a", "expires_at": "not-a-date"})
    got = {t["connection_id"]: t for t in alerts.expiring_tokens(db, now=now)}
    assert set(got) == {soon.id, gone.id}
    assert got[soon.id]["days_left"] == 14
    assert got[gone.id]["days_left"] < 0
    text = alerts.token_expiring(list(got.values())).text()
    assert "in 14 day(s)" in text and "EXPIRED" in text
    assert alerts.token_expiring([]) is None


def test_unreadable_or_deleted_tokens_are_ignored(db):
    c = _conn(db, name="Broken", tokens={"access_token": "a",
                                          "expires_at": "2026-01-01T00:00:00Z"})
    c.encrypted_tokens = b"not-fernet"
    d = _conn(db, name="Deleted", tokens={"access_token": "a",
                                           "expires_at": "2026-01-01T00:00:00Z"})
    d.deleted_at = datetime.now(UTC)
    db.commit()
    assert alerts.expiring_tokens(db) == []


def test_check_tokens_task_sends_token_and_version_alerts(db, sent, monkeypatch):
    soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    _conn(db, tokens={"access_token": "a", "expires_at": soon})
    monkeypatch.setattr(alerts, "api_version_notices", lambda **kw: [
        {"platform": "meta", "version": "v21.0", "sunset": "2027-01-21", "days_left": 79}])
    assert tasks.check_tokens_task() == 2
    assert [a.kind for a in sent] == ["token.expiring", "api.version"]
    assert "retires 2027-01-21" in sent[1].text()


def test_api_version_notice_window(monkeypatch):
    from app.connectors.meta import connector as meta_connector

    # the sunset table still knows v21.0; pin it to exercise the window
    monkeypatch.setattr(meta_connector, "GRAPH_VERSION", "v21.0")
    near = alerts.api_version_notices(today=date(2026, 11, 3))
    assert near == [{"platform": "meta", "version": "v21.0",
                     "sunset": "2027-01-21", "days_left": 79}]
    assert alerts.api_version_notices(today=date(2026, 9, 1)) == []
    assert alerts.api_version_alert([]) is None


# ------------------------------------------------------- the daily sweeps
def test_freshness_task_alerts_on_stale(db, sent):
    c = _conn(db)
    acc = Account(connection_id=c.id, platform="meta", external_id="a1", name="A1")
    db.add(acc)
    db.flush()
    db.add(_fact(acc, date.today() - timedelta(days=10), 5))
    db.commit()
    assert tasks.check_freshness_task() == 1
    assert sent[0].kind == "data.stale" and "Main" in sent[0].text()


def test_budget_task_alerts_on_overspend(db, sent):
    c = _conn(db)
    acc = Account(connection_id=c.id, platform="meta", external_id="a1",
                  name="A1", currency="USD")
    db.add(acc)
    db.flush()
    db.add(_fact(acc, date.today(), 100_000, currency="USD"))
    db.add(Budget(user_id=c.user_id, name="Tiny", amount_monthly=10,
                  currency="USD", enabled=True))
    db.commit()
    assert tasks.check_budgets_task() == 1
    assert sent[0].kind == "budget.over" and "Tiny" in sent[0].text()


def test_anomaly_task_alerts_on_spike(db, sent):
    c = _conn(db)
    acc = Account(connection_id=c.id, platform="meta", external_id="a1",
                  name="Spiky", currency="USD")
    db.add(acc)
    db.flush()
    y = date.today() - timedelta(days=1)
    for i in range(1, 6):
        db.add(_fact(acc, y - timedelta(days=i), 100, currency="USD"))
    db.add(_fact(acc, y, 1000, currency="USD"))
    db.commit()
    assert tasks.check_anomalies_task() == 1
    assert sent[0].kind == "spend.anomaly" and "Spiky" in sent[0].text()


def test_sweeps_quiet_when_nothing_wrong(db, sent):
    assert tasks.check_freshness_task() == 0
    assert tasks.check_budgets_task() == 0
    assert tasks.check_anomalies_task() == 0
    assert sent == []


# ------------------------------------------------------------------ digest
def test_daily_digest_summarises_without_blending(db, sent):
    now = datetime.now(UTC)
    c = _conn(db)
    snap = _conn(db, user_id=c.user_id, platform="snapchat", name="Snap")
    acc = Account(connection_id=c.id, platform="meta", external_id="a1", name="A1")
    sacc = Account(connection_id=snap.id, platform="snapchat", external_id="s1",
                   name="S1")
    db.add_all([acc, sacc])
    db.flush()
    y = (now - timedelta(days=1)).date()
    db.add_all([_fact(acc, y, 1200.5, "SAR"), _fact(sacc, y, 300, "USD")])
    db.add_all([
        SyncJob(connection_id=c.id, date_from=y, date_to=y, status="success",
                stats={"accounts_skipped": 1}),
        SyncJob(connection_id=c.id, date_from=y, date_to=y, status="failed"),
        SyncJob(connection_id=snap.id, date_from=y, date_to=y, status="success",
                stats={"accounts_skipped": 0}),
    ])
    db.commit()
    assert tasks.daily_digest_task() is True
    d = sent[0]
    assert d.kind == "digest.daily"
    text = d.text()
    assert "syncs meta: failed 1, success 1" in text
    assert "syncs snapchat: success 1" in text
    assert "syncs with skipped accounts: 1" in text
    # per currency, never summed across SAR and USD
    assert "meta 1,200.50 SAR" in text and "snapchat 300.00 USD" in text
    assert d.payload["syncs_with_skips"] == 1


def test_daily_digest_when_idle(db):
    d = alerts.daily_digest(db, now=datetime(2026, 11, 3, 5, tzinfo=UTC))
    text = d.text()
    assert "syncs: none ran" in text
    assert "no rows" in text
    # majra now pins Graph v26.0, whose sunset isn't published: no notice
    assert "API version notice" not in text


# ------------------------------------------------------------ other tasks
def test_tick_and_reports_tasks(db, monkeypatch):
    queued = []
    monkeypatch.setattr(tasks, "tick_schedules", lambda db: [7, 8])
    monkeypatch.setattr(tasks.run_sync_task, "delay", lambda jid: queued.append(jid))
    assert tasks.tick_schedules_task() == 2
    assert queued == [7, 8]
    monkeypatch.setattr(tasks, "run_due_reports", lambda db: [1])
    assert tasks.run_reports_task() == 1


def test_fx_task_survives_provider_failure(monkeypatch):
    monkeypatch.setattr(tasks, "refresh_fx_rates", lambda db: 3)
    assert tasks.refresh_fx_task() == 3

    def boom(db):
        raise RuntimeError("provider down")
    monkeypatch.setattr(tasks, "refresh_fx_rates", boom)
    assert tasks.refresh_fx_task() == 0


def test_beat_schedules_digest_and_token_check():
    sched = tasks.celery_app.conf.beat_schedule
    assert sched["daily-digest"]["task"].endswith("daily_digest_task")
    assert sched["check-tokens"]["task"].endswith("check_tokens_task")


# ------------------------------------------------------------ /alerts/test
@respx.mock
def test_test_endpoint_reports_each_recipient(auth_client, whatsapp):
    respx.post(WA_URL).mock(side_effect=[
        httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}),
        httpx.Response(400, json={"error": {
            "code": 131030, "message": "Recipient phone number not in allowed list",
            "fbtrace_id": "x"}}),
    ])
    body = auth_client.post("/alerts/test").json()
    assert body["channels"] == ["whatsapp"]
    first, second = body["whatsapp"]
    assert first == {"to": "+…001", "ok": True, "status": 200, "error": None}
    assert second["ok"] is False and second["status"] == 400
    assert second["error"] == {"code": 131030,
                               "message": "Recipient phone number not in allowed list"}
    assert "wa-secret-token" not in str(body)
    assert "966500000002" not in str(body)   # recipients are masked


@respx.mock
def test_test_endpoint_with_nothing_configured(auth_client):
    body = auth_client.post("/alerts/test").json()
    assert body == {"channels": [], "whatsapp": None, "webhook": None}


def test_test_endpoint_is_owner_only(client, auth_client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(config.settings, "allow_registration", True)
    r = client.post("/auth/register", json={"email": "second@majra.dev",
                                            "password": "s3cret-pass"})
    other = TestClient(app)
    other.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    assert other.post("/alerts/test").status_code == 403
