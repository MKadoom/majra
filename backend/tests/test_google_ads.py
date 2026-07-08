"""Google Ads connector: normalization units + end-to-end sync against a mocked
Google Ads REST API, plus a blended Meta+Google /metrics query (the Phase-4
proof that two platforms answer from one query with no core changes)."""
import json
from datetime import date, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.connectors.google_ads.connector import API_VERSION, GoogleAdsConnector
from app.core.security import encrypt_tokens
from app.models import Connection, FactAdPerformance, RawGoogleInsights
from app.services.sync import watermark

GADS = f"https://googleads.googleapis.com/{API_VERSION}"
OAUTH = "https://oauth2.googleapis.com/token"
CID = "1112223333"


# ---------------------------------------------------------------- unit tests
def _gads_insight(day, cost_micros, conversions, value, device=None, country=None):
    seg = {"date": day}
    if device:
        seg["device"] = device
    if country:
        seg["geoTargetCountry"] = country
    return {
        "campaign": {"id": "111", "name": "GCamp"},
        "adGroup": {"id": "211", "name": "GGroup"},
        "adGroupAd": {"ad": {"id": "311", "name": "GAd"}},
        "segments": seg,
        "metrics": {
            "costMicros": cost_micros, "impressions": "1000", "clicks": "50",
            "conversions": conversions, "conversionsValue": value, "videoViews": "7",
        },
    }


def test_google_normalize_insight():
    n = GoogleAdsConnector().normalize_insight(
        _gads_insight("2026-06-01", "10500000", 2.0, 100.0), "none"
    )
    assert n["date"] == "2026-06-01"
    assert n["spend"] == 10.5                    # cost_micros / 1e6
    assert n["impressions"] == 1000 and n["clicks"] == 50
    assert n["conversions"] == 2.0 and n["conversion_value"] == 100.0
    assert n["video_views"] == 7
    assert n["campaign_external_id"] == "111" and n["adset_external_id"] == "211"
    assert n["ad_external_id"] == "311"
    assert n["breakdown"] == "none"


def test_google_normalize_device_breakdown():
    n = GoogleAdsConnector().normalize_insight(
        _gads_insight("2026-06-01", "5000000", 1.0, 50.0, device="MOBILE"), "device"
    )
    assert n["device"] == "mobile" and n["breakdown"] == "device"


def test_google_normalize_entities():
    c = GoogleAdsConnector()
    camp = c.normalize_entity(
        {"campaign": {"id": "111", "name": "GCamp", "status": "ENABLED",
                      "advertisingChannelType": "SEARCH"}}, "campaign"
    )
    assert camp["external_id"] == "111" and camp["objective"] == "SEARCH"
    ag = c.normalize_entity(
        {"adGroup": {"id": "211", "name": "GGroup", "status": "ENABLED"},
         "campaign": {"id": "111"}}, "adset"
    )
    assert ag["external_id"] == "211" and ag["campaign_external_id"] == "111"
    ad = c.normalize_entity(
        {"adGroupAd": {"ad": {"id": "311", "name": "GAd"}, "status": "ENABLED"},
         "adGroup": {"id": "211"}}, "ad"
    )
    assert ad["external_id"] == "311" and ad["adset_external_id"] == "211"


def test_google_unsupported_breakdown_raises():
    from app.connectors.base import PermanentError
    with pytest.raises(PermanentError):
        list(
            GoogleAdsConnector().fetch_insights(
                None, CID, date(2026, 6, 1), date(2026, 6, 2), "age_gender"
            )
        )


# --------------------------------------------------------- mocked API wiring
def _google_search_router(request: httpx.Request) -> httpx.Response:
    """One searchStream URL serves customer/campaign/ad_group/ad/insights; branch
    on the GAQL query. Order matters: check 'metrics.' (insights) and
    'ad_group_ad' before the 'ad_group' substring."""
    q = json.loads(request.content)["query"]
    if "FROM customer" in q:
        return httpx.Response(200, json=[{"results": [
            {"customer": {"id": CID, "descriptiveName": "Main SAR",
                          "currencyCode": "SAR", "timeZone": "Asia/Riyadh"}}
        ]}])
    if "metrics." in q:  # insights
        return httpx.Response(200, json=[{"results": [
            _gads_insight("2026-06-01", "10500000", 2.0, 100.0),
            _gads_insight("2026-06-02", "3000000", 1.0, 50.0),
        ]}])
    if "FROM campaign" in q:
        return httpx.Response(200, json=[{"results": [
            {"campaign": {"id": "111", "name": "GCamp", "status": "ENABLED",
                          "advertisingChannelType": "SEARCH"}}
        ]}])
    if "FROM ad_group_ad" in q:  # ad entities (no metrics)
        return httpx.Response(200, json=[{"results": [
            {"adGroupAd": {"ad": {"id": "311", "name": "GAd"}, "status": "ENABLED"},
             "adGroup": {"id": "211"}}
        ]}])
    if "FROM ad_group" in q:
        return httpx.Response(200, json=[{"results": [
            {"adGroup": {"id": "211", "name": "GGroup", "status": "ENABLED"},
             "campaign": {"id": "111"}}
        ]}])
    return httpx.Response(200, json=[{"results": []}])


def _mock_google():
    respx.post(OAUTH).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.fresh",
                                               "expires_in": 3600})
    )
    respx.get(f"{GADS}/customers:listAccessibleCustomers").mock(
        return_value=httpx.Response(200, json={"resourceNames": [f"customers/{CID}"]})
    )
    respx.post(f"{GADS}/customers/{CID}/googleAds:searchStream").mock(
        side_effect=_google_search_router
    )


@pytest.fixture
def google_env(monkeypatch):
    monkeypatch.setenv("MAJRA_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("MAJRA_GOOGLE_CLIENT_SECRET", "gsecret")
    monkeypatch.setenv("MAJRA_GOOGLE_ADS_DEVELOPER_TOKEN", "devtoken")


@pytest.fixture
def google_connection(auth_client, db):
    user_id = auth_client.get("/auth/me").json()["id"]
    conn = Connection(
        user_id=user_id, platform="google_ads", name="test google",
        encrypted_tokens=encrypt_tokens(
            {"access_token": "", "refresh_token": "1//refresh"}
        ),
    )
    db.add(conn)
    db.commit()
    return conn


@respx.mock
def test_google_full_sync_and_metrics(auth_client, db, google_env, google_connection):
    _mock_google()
    r = auth_client.post("/syncs", json={
        "connection_id": google_connection.id,
        "date_from": "2026-06-01", "date_to": "2026-06-02", "breakdown": "none",
    })
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "success", job["error"]
    assert job["stats"]["rows_inserted"] == 2

    assert db.scalar(select(func.count()).select_from(FactAdPerformance)) == 2
    assert db.scalar(select(func.count()).select_from(RawGoogleInsights)) == 2
    assert db.scalars(select(FactAdPerformance.currency).distinct()).all() == ["SAR"]
    assert watermark(db, google_connection.id, "none") == date(2026, 6, 2)

    # metrics totals match the mocked "UI": spend 13.5, conversions 3, value 150
    r = auth_client.get("/metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02"})
    row = r.json()["data"][0]
    assert row["spend"] == 13.5
    assert row["conversions"] == 3.0 and row["conversion_value"] == 150.0
    assert row["roas"] == round(150.0 / 13.5, 4)

    # re-sync is idempotent
    r2 = auth_client.post("/syncs", json={
        "connection_id": google_connection.id,
        "date_from": "2026-06-01", "date_to": "2026-06-02", "breakdown": "none"})
    assert r2.json()["stats"]["rows_deleted"] == 2
    assert db.scalar(select(func.count()).select_from(FactAdPerformance)) == 2


# ------------------------------- the Phase-4 proof: blended in ONE query -----
GRAPH = "https://graph.facebook.com/v21.0"


def _mock_meta_minimal():
    respx.get(f"{GRAPH}/me/adaccounts").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "act_9", "name": "Meta SAR", "currency": "SAR",
                  "timezone_name": "Asia/Riyadh"}]}))
    respx.get(f"{GRAPH}/act_9/campaigns").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "mc1", "name": "MCamp", "status": "ACTIVE",
                  "objective": "OUTCOME_SALES"}]}))
    respx.get(f"{GRAPH}/act_9/adsets").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "ms1", "name": "MSet", "status": "ACTIVE",
                  "campaign_id": "mc1"}]}))
    respx.get(f"{GRAPH}/act_9/ads").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "ma1", "name": "MAd", "status": "ACTIVE", "adset_id": "ms1"}]}))
    respx.get(f"{GRAPH}/act_9/insights").mock(return_value=httpx.Response(200, json={
        "data": [{"date_start": "2026-06-01", "campaign_id": "mc1",
                  "campaign_name": "MCamp", "adset_id": "ms1", "adset_name": "MSet",
                  "ad_id": "ma1", "ad_name": "MAd", "spend": "5.0",
                  "impressions": "500", "clicks": "25",
                  "actions": [{"action_type": "omni_purchase", "value": "1"}],
                  "action_values": [{"action_type": "omni_purchase", "value": "40"}]}]}))


@respx.mock
def test_blended_meta_and_google_single_query(auth_client, db, google_env):
    _mock_google()
    _mock_meta_minimal()
    user_id = auth_client.get("/auth/me").json()["id"]

    g = Connection(user_id=user_id, platform="google_ads", name="g",
                   encrypted_tokens=encrypt_tokens(
                       {"access_token": "", "refresh_token": "1//r"}))
    m = Connection(user_id=user_id, platform="meta", name="m",
                   encrypted_tokens=encrypt_tokens({"access_token": "fake"}))
    db.add_all([g, m])
    db.commit()

    for conn in (g, m):
        r = auth_client.post("/syncs", json={
            "connection_id": conn.id, "date_from": "2026-06-01",
            "date_to": "2026-06-02", "breakdown": "none"})
        assert r.json()["status"] == "success", r.json()["error"]

    # ONE query, grouped by platform, returns both platforms blended
    r = auth_client.get("/metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02",
        "group_by": ["platform"], "sort": "-spend"})
    data = r.json()["data"]
    by_platform = {d["platform"]: d["spend"] for d in data}
    assert by_platform == {"google_ads": 13.5, "meta": 5.0}
    assert r.json()["total"] == 2

    # blended grand total across both platforms in one row
    r = auth_client.get("/metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02"})
    assert r.json()["data"][0]["spend"] == 18.5   # 13.5 + 5.0
