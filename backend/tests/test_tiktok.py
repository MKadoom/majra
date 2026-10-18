"""TikTok Ads connector: normalization units + end-to-end sync against a mocked
Business API. Also verifies the engine derives campaign/adgroup for facts even
though the ad-level report carries only ad_id."""
import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.connectors.base import PermanentError
from app.connectors.tiktok.connector import BASE, TikTokConnector
from app.core.security import encrypt_tokens
from app.models import Connection, FactAdPerformance, RawTikTokReport


def test_tiktok_auth_url_requires_config():
    """Without app credentials, starting the OAuth flow fails with an actionable
    message rather than emitting a broken consent URL (empty app_id)."""
    c = TikTokConnector()
    c._cfg.tiktok_app_id = ""
    c._cfg.tiktok_app_secret = ""
    with pytest.raises(PermanentError, match="not configured"):
        c.auth_url("state", "https://example.test/connect/tiktok/callback")

    c._cfg.tiktok_app_id = "app123"
    c._cfg.tiktok_app_secret = "shh"
    url = c.auth_url("state", "https://example.test/connect/tiktok/callback")
    assert "app_id=app123" in url and "state=state" in url


def _row(day, spend, imp, clicks, conv, vv, payments=None, value=None):
    metrics = {"spend": spend, "impressions": imp, "clicks": clicks,
               "conversion": conv, "video_play_actions": vv}
    if payments is not None:
        metrics["complete_payment"] = payments
    if value is not None:
        metrics["total_complete_payment_rate"] = value
    return {
        "dimensions": {"ad_id": "a1", "stat_time_day": f"{day} 00:00:00"},
        "metrics": metrics,
    }


def test_tiktok_normalize_insight():
    n = TikTokConnector().normalize_insight(
        _row("2026-06-01", "10.5", "1000", "50", "2", "300",
             payments="2", value="180.75"), "none"
    )
    assert n["date"] == "2026-06-01"
    assert n["ad_external_id"] == "a1"
    assert n["campaign_external_id"] is None      # report has no campaign id
    assert n["spend"] == 10.5 and n["impressions"] == 1000 and n["clicks"] == 50
    assert n["conversions"] == 2.0 and n["video_views"] == 300
    assert n["purchases"] == 2
    assert n["conversion_value"] == 180.75


def test_tiktok_normalize_insight_without_payment_metrics():
    """Ads with no pixel purchases report no payment metrics — default to 0."""
    n = TikTokConnector().normalize_insight(
        _row("2026-06-01", "10.5", "1000", "50", "2", "300"), "none"
    )
    assert n["purchases"] == 0
    assert n["conversion_value"] == 0.0


def test_tiktok_report_requests_payment_metrics():
    from app.connectors.tiktok.connector import REPORT_METRICS
    assert "complete_payment" in REPORT_METRICS
    assert "total_complete_payment_rate" in REPORT_METRICS


def test_tiktok_normalize_entities():
    c = TikTokConnector()
    camp = c.normalize_entity(
        {"campaign_id": "c1", "campaign_name": "TCamp",
         "operation_status": "ENABLE", "objective_type": "CONVERSIONS"}, "campaign")
    assert camp["external_id"] == "c1" and camp["objective"] == "CONVERSIONS"
    ag = c.normalize_entity(
        {"adgroup_id": "g1", "adgroup_name": "TGroup", "campaign_id": "c1"}, "adset")
    assert ag["external_id"] == "g1" and ag["campaign_external_id"] == "c1"
    ad = c.normalize_entity(
        {"ad_id": "a1", "ad_name": "TAd", "adgroup_id": "g1"}, "ad")
    assert ad["external_id"] == "a1" and ad["adset_external_id"] == "g1"


def _ok(data):
    return httpx.Response(200, json={"code": 0, "message": "OK", "data": data})


@respx.mock
def test_tiktok_fetch_accounts_via_business_center():
    """When /oauth2/advertiser/get/ returns no directly-authorized advertisers
    (the common Business-Center-managed case), discovery falls back to
    /bc/get/ + /bc/asset/get/ so the BC's ad accounts are still found."""
    from app.connectors.tiktok.connector import TokenSet

    respx.get(f"{BASE}/oauth2/advertiser/get/").mock(return_value=_ok({"list": []}))
    respx.get(f"{BASE}/bc/get/").mock(
        return_value=_ok({"list": [{"bc_id": "BC1"}], "page_info": {"page": 1, "total_page": 1}}))
    respx.get(f"{BASE}/bc/asset/get/").mock(
        return_value=_ok({"list": [{"advertiser_id": "7479812335633498113"}],
                          "page_info": {"page": 1, "total_page": 1}}))
    respx.get(f"{BASE}/advertiser/info/").mock(
        return_value=_ok({"list": [{"advertiser_id": "7479812335633498113",
                                    "name": "جمعية الثريا", "currency": "SAR",
                                    "timezone": "Asia/Riyadh"}]}))
    c = TikTokConnector()
    c._cfg.tiktok_app_id = "app"
    c._cfg.tiktok_app_secret = "shh"
    accts = c.fetch_accounts(TokenSet(access_token="tok"))
    assert len(accts) == 1
    assert accts[0].external_id == "7479812335633498113"
    assert accts[0].currency == "SAR"


def _pi(data_list):
    return {"list": data_list, "page_info": {"page": 1, "total_page": 1}}


def _mock_tiktok():
    respx.get(f"{BASE}/oauth2/advertiser/get/").mock(
        return_value=_ok({"list": [{"advertiser_id": "adv1", "advertiser_name": "Adv"}]}))
    respx.get(f"{BASE}/advertiser/info/").mock(
        return_value=_ok({"list": [{"advertiser_id": "adv1", "name": "Adv One",
                                    "currency": "SAR", "timezone": "Asia/Riyadh"}]}))
    respx.get(f"{BASE}/campaign/get/").mock(return_value=_ok(_pi(
        [{"campaign_id": "c1", "campaign_name": "TCamp",
          "operation_status": "ENABLE", "objective_type": "CONVERSIONS"}])))
    respx.get(f"{BASE}/adgroup/get/").mock(return_value=_ok(_pi(
        [{"adgroup_id": "g1", "adgroup_name": "TGroup",
          "campaign_id": "c1", "operation_status": "ENABLE"}])))
    respx.get(f"{BASE}/ad/get/").mock(return_value=_ok(_pi(
        [{"ad_id": "a1", "ad_name": "TAd", "adgroup_id": "g1",
          "campaign_id": "c1", "operation_status": "ENABLE"}])))
    respx.get(f"{BASE}/report/integrated/get/").mock(return_value=_ok(_pi(
        [_row("2026-06-01", "10.5", "1000", "50", "2", "300",
              payments="2", value="40.0"),
         _row("2026-06-02", "5.0", "500", "25", "1", "150",
              payments="1", value="22.0")])))


@pytest.fixture
def tiktok_connection(auth_client, db):
    user_id = auth_client.get("/auth/me").json()["id"]
    conn = Connection(user_id=user_id, platform="tiktok", name="tt",
                      encrypted_tokens=encrypt_tokens({"access_token": "tok"}))
    db.add(conn)
    db.commit()
    return conn


@respx.mock
def test_tiktok_full_sync_derives_hierarchy(auth_client, db, tiktok_connection):
    _mock_tiktok()
    body = {"connection_id": tiktok_connection.id, "date_from": "2026-06-01",
            "date_to": "2026-06-02", "breakdown": "none"}
    r = auth_client.post("/syncs", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "success", r.json()["error"]
    assert r.json()["stats"]["rows_inserted"] == 2

    assert db.scalar(select(func.count()).select_from(RawTikTokReport)) == 2
    # the report only had ad_id, but the engine derived campaign + adgroup:
    fact = db.scalars(select(FactAdPerformance)).first()
    assert fact.ad_id is not None
    assert fact.adset_id is not None
    assert fact.campaign_id is not None

    # so group_by=campaign works and shows the campaign name + total spend
    r = auth_client.get("/metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02", "group_by": ["campaign"]})
    row = r.json()["data"][0]
    assert row["campaign"] == "TCamp" and row["spend"] == 15.5
    assert row["conversion_value"] == 62.0 and row["purchases"] == 3
    assert row["roas"] == pytest.approx(62.0 / 15.5, rel=1e-3)

    # idempotent re-sync
    r2 = auth_client.post("/syncs", json=body)
    assert r2.json()["stats"]["rows_deleted"] == 2
    assert db.scalar(select(func.count()).select_from(FactAdPerformance)) == 2
