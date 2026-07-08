"""Snapchat Ads connector: normalization units + end-to-end sync against a
mocked Marketing API (token refresh, nested timeseries stats, micro-currency).
Also verifies campaign/ad-squad derivation for ad-level stats."""
import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.connectors.snapchat.connector import API, TOKEN, SnapchatConnector
from app.core.security import encrypt_tokens
from app.models import Connection, FactAdPerformance, RawSnapchatStats


def test_snapchat_normalize_insight_micro_currency():
    n = SnapchatConnector().normalize_insight(
        {"ad_id": "a1", "start_time": "2026-06-01T00:00:00.000-00:00",
         "stats": {"spend": 10_500_000, "impressions": 1000, "swipes": 50,
                   "conversion_purchases": 2, "conversion_purchases_value": 100_000_000}},
        "none",
    )
    assert n["date"] == "2026-06-01"
    assert n["spend"] == 10.5                       # 10_500_000 / 1e6
    assert n["clicks"] == 50                        # swipes -> clicks
    assert n["conversions"] == 2.0
    assert n["conversion_value"] == 100.0           # 100_000_000 / 1e6
    assert n["ad_external_id"] == "a1"
    assert n["campaign_external_id"] is None


def test_snapchat_normalize_entities():
    c = SnapchatConnector()
    sq = c.normalize_entity(
        {"id": "sq1", "name": "SSquad", "campaign_id": "c1", "status": "ACTIVE"}, "adset")
    assert sq["external_id"] == "sq1" and sq["campaign_external_id"] == "c1"
    ad = c.normalize_entity(
        {"id": "a1", "name": "SAd", "ad_squad_id": "sq1", "status": "ACTIVE"}, "ad")
    assert ad["external_id"] == "a1" and ad["adset_external_id"] == "sq1"


def _point(day, spend, imp, swipes, conv, value):
    return {"start_time": f"{day}T00:00:00.000-00:00",
            "stats": {"spend": spend, "impressions": imp, "swipes": swipes,
                      "conversion_purchases": conv, "conversion_purchases_value": value}}


def _mock_snapchat():
    respx.post(TOKEN).mock(return_value=httpx.Response(
        200, json={"access_token": "fresh", "refresh_token": "r", "expires_in": 1800}))
    respx.get(f"{API}/me/organizations").mock(return_value=httpx.Response(
        200, json={"organizations": [{"organization": {"id": "org1", "name": "Org"}}]}))
    respx.get(f"{API}/organizations/org1/adaccounts").mock(return_value=httpx.Response(
        200, json={"adaccounts": [{"adaccount": {"id": "acc1", "name": "SAcc",
                                                 "currency": "SAR", "timezone": "Asia/Riyadh"}}]}))
    respx.get(f"{API}/adaccounts/acc1/campaigns").mock(return_value=httpx.Response(
        200, json={"campaigns": [{"campaign": {"id": "c1", "name": "SCamp",
                                               "status": "ACTIVE"}}]}))
    respx.get(f"{API}/adaccounts/acc1/adsquads").mock(return_value=httpx.Response(
        200, json={"adsquads": [{"adsquad": {"id": "sq1", "name": "SSquad",
                                             "campaign_id": "c1", "status": "ACTIVE"}}]}))
    respx.get(f"{API}/adaccounts/acc1/ads").mock(return_value=httpx.Response(
        200, json={"ads": [{"ad": {"id": "a1", "name": "SAd",
                                   "ad_squad_id": "sq1", "status": "ACTIVE"}}]}))
    respx.get(f"{API}/adaccounts/acc1/stats").mock(return_value=httpx.Response(
        200, json={"timeseries_stats": [{"timeseries_stat": {
            "id": "acc1", "type": "AD_ACCOUNT", "granularity": "DAY",
            "breakdown_stats": {"ad": [{"id": "a1", "type": "AD", "timeseries": [
                _point("2026-06-01", 10_500_000, 1000, 50, 2, 100_000_000),
                _point("2026-06-02", 5_000_000, 500, 25, 1, 50_000_000),
            ]}]},
        }}]}))


@pytest.fixture
def snap_connection(auth_client, db):
    user_id = auth_client.get("/auth/me").json()["id"]
    conn = Connection(user_id=user_id, platform="snapchat", name="snap",
                      encrypted_tokens=encrypt_tokens(
                          {"access_token": "old", "refresh_token": "r"}))
    db.add(conn)
    db.commit()
    return conn


@respx.mock
def test_snapchat_full_sync_derives_hierarchy(auth_client, db, snap_connection):
    _mock_snapchat()
    body = {"connection_id": snap_connection.id, "date_from": "2026-06-01",
            "date_to": "2026-06-02", "breakdown": "none"}
    r = auth_client.post("/syncs", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "success", r.json()["error"]
    assert r.json()["stats"]["rows_inserted"] == 2

    assert db.scalar(select(func.count()).select_from(RawSnapchatStats)) == 2
    fact = db.scalars(select(FactAdPerformance)).first()
    assert fact.campaign_id is not None and fact.adset_id is not None

    # totals: spend 15.5, clicks 75 (swipes), conversions 3, value 150
    r = auth_client.get("/metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02"})
    row = r.json()["data"][0]
    assert row["spend"] == 15.5 and row["clicks"] == 75
    assert row["conversions"] == 3.0 and row["conversion_value"] == 150.0

    # by campaign
    r = auth_client.get("/metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02", "group_by": ["campaign"]})
    assert r.json()["data"][0]["campaign"] == "SCamp"

    # idempotent
    _mock_snapchat()
    r2 = auth_client.post("/syncs", json=body)
    assert r2.json()["stats"]["rows_deleted"] == 2
    assert db.scalar(select(func.count()).select_from(FactAdPerformance)) == 2
