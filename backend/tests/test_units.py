from app.connectors.meta.connector import MetaConnector
from app.core.security import decrypt_tokens, encrypt_tokens


def test_health(client):
    body = client.get("/health").json()
    assert body["db"] is True
    assert body["status"] == "ok"
    assert body["environment"] == "test"


def test_auth_flow(client):
    r = client.post(
        "/auth/register", json={"email": "a@b.co", "password": "longenough"}
    )
    assert r.status_code == 201
    tokens = r.json()

    # duplicate email rejected
    assert (
        client.post(
            "/auth/register", json={"email": "a@b.co", "password": "longenough"}
        ).status_code
        == 409
    )

    # login + me
    r = client.post("/auth/login", json={"email": "a@b.co", "password": "longenough"})
    assert r.status_code == 200
    access = r.json()["access_token"]
    me = client.get("/me" if False else "/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200 and me.json()["email"] == "a@b.co"

    # wrong password
    assert (
        client.post("/auth/login", json={"email": "a@b.co", "password": "nope-nope"}).status_code
        == 401
    )

    # refresh rotates a new pair
    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200 and "access_token" in r.json()

    # access token is not a refresh token
    assert (
        client.post("/auth/refresh", json={"refresh_token": access}).status_code == 401
    )


def test_token_encryption_roundtrip():
    payload = {"access_token": "tok-123", "extra": {"scope": "ads_read"}}
    blob = encrypt_tokens(payload)
    assert isinstance(blob, bytes) and b"tok-123" not in blob
    assert decrypt_tokens(blob) == payload


FIXTURE_ROW = {
    "date_start": "2026-06-01",
    "date_stop": "2026-06-01",
    "campaign_id": "c1",
    "campaign_name": "Ramadan Push",
    "adset_id": "s1",
    "adset_name": "Jazan 18-34",
    "ad_id": "a1",
    "ad_name": "Video A",
    "spend": "10.50",
    "impressions": "1000",
    "clicks": "50",
    "actions": [
        {"action_type": "omni_purchase", "value": "2"},
        {"action_type": "lead", "value": "1"},
        {"action_type": "link_click", "value": "40"},
    ],
    "action_values": [{"action_type": "omni_purchase", "value": "150.0"}],
    "video_play_actions": [{"action_type": "video_view", "value": "300"}],
}


def test_meta_normalize_insight():
    n = MetaConnector().normalize_insight(FIXTURE_ROW, "none")
    assert n["date"] == "2026-06-01"
    assert n["spend"] == 10.5
    assert n["impressions"] == 1000 and n["clicks"] == 50
    assert n["purchases"] == 2 and n["leads"] == 1
    assert n["conversions"] == 3  # purchases + leads; link_click excluded
    assert n["conversion_value"] == 150.0
    assert n["video_views"] == 300
    assert n["breakdown"] == "none" and n["country"] is None


def test_meta_normalize_country_breakdown():
    row = {**FIXTURE_ROW, "country": "SA"}
    n = MetaConnector().normalize_insight(row, "country")
    assert n["country"] == "SA" and n["breakdown"] == "country"
