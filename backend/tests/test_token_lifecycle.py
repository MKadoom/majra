"""T3 — token lifecycle.

- The engine refreshes a token before it lapses and stores the result encrypted
  (a rotated refresh token is never lost).
- Every connector reports dead credentials as AuthError, which flags the
  connection needs_reauth; a per-account permission error is still just a skip.
- Reconnecting (OAuth or service account) reuses the existing connection, so
  its id, schedule and history survive.
"""
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.connectors.base import AuthError, PermanentError, TokenSet
from app.core.security import decrypt_tokens, encrypt_tokens
from app.models import Connection, Schedule, SyncJob
from app.services.sync import refresh_tokens_if_due, run_sync

GRAPH = "https://graph.facebook.com/v26.0"
SNAP_TOKEN = "https://accounts.snapchat.com/login/oauth2/access_token"
SNAP_API = "https://adsapi.snapchat.com/v1"


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


@pytest.fixture
def owner_id(auth_client):
    return auth_client.get("/auth/me").json()["id"]


def _conn(db, user_id, platform, tokens: dict, status="active") -> Connection:
    c = Connection(user_id=user_id, platform=platform, name=f"{platform} conn",
                   encrypted_tokens=encrypt_tokens(tokens), status=status)
    db.add(c)
    db.commit()
    return c


def _stored(db, conn) -> dict:
    db.expire_all()
    return decrypt_tokens(db.get(Connection, conn.id).encrypted_tokens)


# ------------------------------------------------------ needs_refresh rules
def test_refresh_token_platforms_refresh_when_expiring():
    from app.connectors.snapchat.connector import SnapchatConnector

    c = SnapchatConnector()
    assert c.needs_refresh(TokenSet("a", "r", _iso(timedelta(minutes=5))))
    assert c.needs_refresh(TokenSet("a", "r", None))           # unknown expiry
    assert not c.needs_refresh(TokenSet("a", "r", _iso(timedelta(hours=1))))
    assert not c.needs_refresh(TokenSet("a", None, _iso(timedelta(minutes=1))))


def test_meta_refreshes_two_weeks_ahead():
    from app.connectors.meta.connector import MetaConnector

    m = MetaConnector()
    assert m.needs_refresh(TokenSet("a", expires_at=_iso(timedelta(days=10))))
    assert not m.needs_refresh(TokenSet("a", expires_at=_iso(timedelta(days=30))))
    assert not m.needs_refresh(TokenSet("a"))  # no expiry recorded
    assert not m.needs_refresh(TokenSet("a", expires_at="garbage"))


# ------------------------------------------------------ engine refresh
@respx.mock
def test_engine_refreshes_and_stores_rotated_token(db, owner_id):
    from app.connectors.snapchat.connector import SnapchatConnector

    conn = _conn(db, owner_id, "snapchat", {
        "access_token": "old-access", "refresh_token": "old-refresh",
        "expires_at": _iso(timedelta(minutes=-30)), "extra": {"k": "v"}})
    route = respx.post(SNAP_TOKEN).mock(return_value=httpx.Response(200, json={
        "access_token": "new-access", "refresh_token": "rotated-refresh",
        "expires_in": 1800}))
    tokens = TokenSet.from_dict(decrypt_tokens(conn.encrypted_tokens))
    fresh = refresh_tokens_if_due(db, conn, SnapchatConnector(), tokens)

    assert route.call_count == 1
    assert fresh.access_token == "new-access"
    stored = _stored(db, conn)
    assert stored["access_token"] == "new-access"
    assert stored["refresh_token"] == "rotated-refresh"
    assert stored["extra"] == {"k": "v"}          # kept across the refresh
    assert datetime.fromisoformat(stored["expires_at"]) > datetime.now(UTC)
    # and it is stored encrypted, not as plain text
    raw = db.get(Connection, conn.id).encrypted_tokens
    assert b"new-access" not in raw and b"rotated-refresh" not in raw


@respx.mock
def test_engine_skips_refresh_for_a_fresh_token(db, owner_id):
    from app.connectors.snapchat.connector import SnapchatConnector

    conn = _conn(db, owner_id, "snapchat", {
        "access_token": "a", "refresh_token": "r",
        "expires_at": _iso(timedelta(hours=1))})
    route = respx.post(SNAP_TOKEN)
    tokens = TokenSet.from_dict(decrypt_tokens(conn.encrypted_tokens))
    assert refresh_tokens_if_due(db, conn, SnapchatConnector(), tokens) is tokens
    assert route.call_count == 0


@respx.mock
def test_snapchat_uses_stored_token_after_engine_refresh(db, owner_id):
    """The connector must not refresh a second time (that would rotate the
    refresh token again and lose the stored one)."""
    from app.connectors.snapchat.connector import SnapchatConnector

    token_route = respx.post(SNAP_TOKEN)
    me = respx.get(f"{SNAP_API}/me/organizations").mock(
        return_value=httpx.Response(200, json={"organizations": []}))
    c = SnapchatConnector()
    c.fetch_accounts(TokenSet("fresh", "r", _iso(timedelta(hours=1))))
    assert token_route.call_count == 0
    assert me.calls[0].request.headers["authorization"] == "Bearer fresh"


@respx.mock
def test_meta_long_lived_token_is_re_exchanged(db, owner_id):
    from app.connectors.meta.connector import MetaConnector

    conn = _conn(db, owner_id, "meta", {
        "access_token": "ll-old", "expires_at": _iso(timedelta(days=5))})
    respx.get(url__startswith=f"{GRAPH}/oauth/access_token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "ll-new", "expires_in": 60 * 86400}))
    tokens = TokenSet.from_dict(decrypt_tokens(conn.encrypted_tokens))
    refresh_tokens_if_due(db, conn, MetaConnector(), tokens)
    stored = _stored(db, conn)
    assert stored["access_token"] == "ll-new"
    assert datetime.fromisoformat(stored["expires_at"]) > datetime.now(UTC) + timedelta(days=50)


@respx.mock
def test_rejected_refresh_flags_needs_reauth(db, owner_id):
    conn = _conn(db, owner_id, "snapchat", {
        "access_token": "a", "refresh_token": "revoked",
        "expires_at": _iso(timedelta(minutes=-1))})
    respx.post(SNAP_TOKEN).mock(return_value=httpx.Response(
        400, json={"error": "invalid_grant"}))
    job = SyncJob(connection_id=conn.id, date_from=date(2026, 6, 1),
                  date_to=date(2026, 6, 1))
    db.add(job)
    db.commit()
    with pytest.raises(AuthError):
        run_sync(db, job.id)
    db.expire_all()
    assert db.get(Connection, conn.id).status == "needs_reauth"
    assert db.get(SyncJob, job.id).status == "failed"


@respx.mock
def test_auth_error_mid_sync_is_not_swallowed_as_a_skip(auth_client, db, owner_id):
    conn = _conn(db, owner_id, "meta", {"access_token": "t"})
    respx.get(f"{GRAPH}/me/adaccounts").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "act_1", "name": "A", "currency": "SAR"}]}))
    respx.get(url__regex=rf"{GRAPH}/act_1/.*").mock(return_value=httpx.Response(
        400, json={"error": {"code": 190, "message": "Session has expired"}}))
    r = auth_client.post("/syncs", json={
        "connection_id": conn.id, "date_from": "2026-06-01", "date_to": "2026-06-01"})
    assert r.json()["status"] == "failed"
    assert "190" in r.json()["error"]
    db.expire_all()
    assert db.get(Connection, conn.id).status == "needs_reauth"


# ------------------------------------------------------ AuthError per connector
@respx.mock
def test_meta_permission_403_is_not_auth():
    from app.connectors.meta.connector import MetaConnector

    respx.get(url__startswith=f"{GRAPH}/x").mock(return_value=httpx.Response(
        403, json={"error": {"code": 200, "message": "no permission on account"}}))
    with pytest.raises(PermanentError) as ei:
        MetaConnector()._get(f"{GRAPH}/x", {})
    assert not isinstance(ei.value, AuthError)


@respx.mock
@pytest.mark.parametrize("platform,setup", [
    ("snapchat", lambda: respx.get(f"{SNAP_API}/me/organizations").mock(
        return_value=httpx.Response(401, text="unauthorized"))),
    ("meta", lambda: respx.get(f"{GRAPH}/me/adaccounts").mock(
        return_value=httpx.Response(400, json={"error": {"code": 190, "message": "x"}}))),
    ("meta_social", lambda: respx.get(url__regex=rf"{GRAPH}/me/accounts.*").mock(
        return_value=httpx.Response(400, json={"error": {"code": 190, "message": "x"}}))),
    ("tiktok", lambda: respx.get(url__regex=r"https://business-api\.tiktok\.com/.*").mock(
        return_value=httpx.Response(200, json={"code": 40105, "message": "token invalid"}))),
])
def test_dead_token_raises_auth_error(platform, setup):
    from app.connectors.base import get_connector

    setup()
    with pytest.raises(AuthError):
        get_connector(platform).fetch_accounts(TokenSet("dead", None, None, {}))


@respx.mock
def test_google_ads_revoked_refresh_token_is_auth_error():
    from app.connectors.google_ads.connector import OAUTH_TOKEN, GoogleAdsConnector

    respx.post(OAUTH_TOKEN).mock(return_value=httpx.Response(
        400, json={"error": "invalid_grant", "error_description": "Token has been revoked"}))
    with pytest.raises(AuthError):
        GoogleAdsConnector().refresh(TokenSet("a", "r"))
    with pytest.raises(AuthError):
        GoogleAdsConnector().refresh(TokenSet("a", None))


@respx.mock
def test_google_ads_401_is_auth_error():
    from app.connectors.google_ads.connector import API, GoogleAdsConnector

    respx.get(url__startswith=API).mock(return_value=httpx.Response(
        401, json={"error": {"message": "UNAUTHENTICATED"}}))
    with pytest.raises(AuthError):
        GoogleAdsConnector()._request("GET", f"{API}/customers:listAccessibleCustomers",
                                      TokenSet("a", None, _iso(timedelta(hours=1))))


@respx.mock
def test_service_account_rejected_key_is_auth_error():
    from app.connectors.google_sa import TOKEN_URI, mint_access
    from tests.test_gtm import SERVICE_ACCOUNT

    respx.post(TOKEN_URI).mock(return_value=httpx.Response(
        400, json={"error": "invalid_grant", "error_description": "key deleted"}))
    with pytest.raises(AuthError):
        mint_access(SERVICE_ACCOUNT, "scope")
    respx.post(TOKEN_URI).mock(return_value=httpx.Response(503, text="down"))
    from app.connectors.base import TransientError
    with pytest.raises(TransientError):
        mint_access(SERVICE_ACCOUNT, "scope")


@respx.mock
@pytest.mark.parametrize("platform", ["ga4", "gtm"])
def test_service_account_platforms_401_is_auth_error(platform):
    from app.connectors.base import get_connector
    from app.connectors.google_sa import TOKEN_URI
    from tests.test_gtm import SERVICE_ACCOUNT

    respx.post(TOKEN_URI).mock(return_value=httpx.Response(
        200, json={"access_token": "ya29", "expires_in": 3600}))
    respx.route(url__regex=r"https://.*googleapis\.com/.*").mock(
        return_value=httpx.Response(401, json={"error": {"message": "nope"}}))
    c = get_connector(platform)
    tokens = c.credentials_from_input(
        {"service_account": SERVICE_ACCOUNT, "property_id": "123"})
    with pytest.raises(AuthError):
        if platform == "ga4":
            list(c.fetch_insights(tokens, "123", date(2026, 6, 1), date(2026, 6, 1), "none"))
        else:
            c.fetch_accounts(tokens)


@respx.mock
def test_whatsapp_expired_token_is_auth_error(monkeypatch):
    from app.connectors.whatsapp.connector import WhatsAppConnector

    c = WhatsAppConnector()
    monkeypatch.setattr(c._cfg, "whatsapp_token", "t")
    respx.get(url__regex=r"https://graph\.facebook\.com/.*").mock(
        return_value=httpx.Response(400, json={"error": {"code": 190, "message": "expired"}}))
    with pytest.raises(AuthError):
        c._get(f"{c._base}/123", None)


# ------------------------------------------------------ scheduler
def test_scheduler_skips_needs_reauth(db, owner_id):
    from app.services.schedules import tick_schedules

    ok = _conn(db, owner_id, "meta", {"access_token": "t"})
    dead = _conn(db, owner_id, "meta", {"access_token": "t"}, status="needs_reauth")
    for c in (ok, dead):
        db.add(Schedule(connection_id=c.id, cadence="daily", hour_utc=0,
                        breakdowns=["none"], backfill_days=7))
    db.commit()
    tick_schedules(db, now=datetime(2026, 6, 10, 0, 5, tzinfo=UTC))
    conns = set(db.scalars(select(SyncJob.connection_id)).all())
    assert conns == {ok.id}


# ------------------------------------------------------ reconnect
def _state_from(auth_url: str) -> str:
    return parse_qs(urlparse(auth_url).query)["state"][0]


def _mock_meta_exchange(token="reconnected-token"):
    respx.get(url__startswith=f"{GRAPH}/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": token, "expires_in": 5184000}))


@respx.mock
def test_oauth_reconnect_reuses_connection_and_schedule(auth_client, db, owner_id):
    conn = _conn(db, owner_id, "meta", {"access_token": "dead"}, status="needs_reauth")
    conn.synced_account_ids = ["act_1217959466650120"]
    db.add(Schedule(connection_id=conn.id, cadence="daily", hour_utc=16,
                    breakdowns=["none"]))
    db.commit()

    r = auth_client.get(f"/connect/meta?connection_id={conn.id}")
    assert r.status_code == 200
    _mock_meta_exchange()
    cb = auth_client.get("/connect/meta/callback",
                         params={"state": _state_from(r.json()["auth_url"]), "code": "c"})
    assert cb.status_code == 200 and "reconnected" in cb.text

    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Connection)) == 1
    again = db.get(Connection, conn.id)
    assert again.status == "active"
    assert again.synced_account_ids == ["act_1217959466650120"]
    assert decrypt_tokens(again.encrypted_tokens)["access_token"] == "reconnected-token"
    assert db.scalar(select(Schedule.connection_id)) == conn.id


@respx.mock
def test_oauth_without_connection_id_still_creates_new(auth_client, db, owner_id):
    _conn(db, owner_id, "meta", {"access_token": "t"})
    r = auth_client.get("/connect/meta")
    _mock_meta_exchange()
    auth_client.get("/connect/meta/callback",
                    params={"state": _state_from(r.json()["auth_url"]), "code": "c"})
    assert db.scalar(select(func.count()).select_from(Connection)) == 2


def test_reconnect_refuses_foreign_or_mismatched_connection(auth_client, db, owner_id):
    from app.core.security import hash_password
    from app.models import User

    other = User(email="other@x.co", password_hash=hash_password("p"))
    db.add(other)
    db.flush()
    foreign = _conn(db, other.id, "meta", {"access_token": "t"})
    snap = _conn(db, owner_id, "snapchat", {"access_token": "t"})
    assert auth_client.get(f"/connect/meta?connection_id={foreign.id}").status_code == 404
    assert auth_client.get(f"/connect/meta?connection_id={snap.id}").status_code == 404
    assert auth_client.get("/connect/meta?connection_id=99999").status_code == 404


@respx.mock
def test_callback_refuses_reconnect_of_deleted_connection(auth_client, db, owner_id):
    conn = _conn(db, owner_id, "meta", {"access_token": "t"})
    r = auth_client.get(f"/connect/meta?connection_id={conn.id}")
    conn.deleted_at = datetime.now(UTC)
    db.commit()
    _mock_meta_exchange()
    cb = auth_client.get("/connect/meta/callback",
                         params={"state": _state_from(r.json()["auth_url"]), "code": "c"})
    assert cb.status_code == 400


def test_service_account_reconnect_in_place(auth_client, db, owner_id):
    from tests.test_gtm import SERVICE_ACCOUNT

    r = auth_client.post("/connections/service-account",
                         json={"platform": "gtm", "service_account": SERVICE_ACCOUNT})
    cid = r.json()["id"]
    db.get(Connection, cid).status = "needs_reauth"
    db.commit()
    r2 = auth_client.post("/connections/service-account", json={
        "platform": "gtm", "service_account": SERVICE_ACCOUNT, "connection_id": cid})
    assert r2.status_code == 201 and r2.json()["id"] == cid
    assert r2.json()["status"] == "active"
    assert db.scalar(select(func.count()).select_from(Connection)) == 1
    bad = auth_client.post("/connections/service-account", json={
        "platform": "gtm", "service_account": SERVICE_ACCOUNT, "connection_id": 999})
    assert bad.status_code == 404


def test_digest_lists_connections_needing_reconnect(db, owner_id):
    from app.services.alerts import daily_digest

    _conn(db, owner_id, "meta", {"access_token": "t"}, status="needs_reauth")
    d = daily_digest(db)
    assert "1 connection(s) need reconnecting (meta · meta conn)" in d.text()
    assert d.payload["needs_reauth"][0]["platform"] == "meta"
