"""Facebook Pages + Instagram connector: pivot units + end-to-end sync against a
mocked Graph API, landing page-grain and ig_user-grain rows in
fact_social_performance and querying them via /social-metrics."""
from datetime import date

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.connectors.meta_social.connector import (
    GRAPH, IG_METRICS, PAGE_METRICS, MetaSocialConnector,
)
from app.core.security import encrypt_tokens
from app.models import Connection, FactSocialPerformance, RawMetaSocial


def _series(name, day_values):
    # day_values: list of (end_time, value)
    return {"name": name, "period": "day",
            "values": [{"value": v, "end_time": t} for t, v in day_values]}


PAGE_INSIGHTS = {"data": [
    _series("page_impressions", [("2026-06-02T07:00:00+0000", 1000),
                                 ("2026-06-03T07:00:00+0000", 1200)]),
    _series("page_impressions_unique", [("2026-06-02T07:00:00+0000", 800),
                                        ("2026-06-03T07:00:00+0000", 900)]),
    _series("page_post_engagements", [("2026-06-02T07:00:00+0000", 50),
                                      ("2026-06-03T07:00:00+0000", 60)]),
    _series("page_fans", [("2026-06-02T07:00:00+0000", 5000),
                          ("2026-06-03T07:00:00+0000", 5010)]),
]}
IG_INSIGHTS = {"data": [
    _series("impressions", [("2026-06-02T07:00:00+0000", 500),
                            ("2026-06-03T07:00:00+0000", 600)]),
    _series("reach", [("2026-06-02T07:00:00+0000", 400),
                      ("2026-06-03T07:00:00+0000", 450)]),
    _series("follower_count", [("2026-06-02T07:00:00+0000", 2000),
                               ("2026-06-03T07:00:00+0000", 2005)]),
]}


# ---------------------------------------------------------------- unit tests
def test_pivot_applies_end_time_minus_one_day():
    rows = list(MetaSocialConnector._pivot(
        PAGE_INSIGHTS["data"], PAGE_METRICS, "page", "page1",
        date(2026, 6, 1), date(2026, 6, 2),
    ))
    assert [r["date"] for r in rows] == ["2026-06-01", "2026-06-02"]  # end_time - 1
    assert rows[0] == {"grain": "page", "external_ref": "page1", "date": "2026-06-01",
                       "impressions": 1000, "reach": 800, "engagement": 50,
                       "followers": 5000}


def test_normalize_insight_passthrough():
    n = MetaSocialConnector().normalize_insight(
        {"grain": "ig_user", "external_ref": "ig1", "date": "2026-06-01",
         "impressions": 500, "reach": 400, "followers": 2000}, "none")
    assert n["grain"] == "ig_user" and n["engagement"] == 0 and n["followers"] == 2000


# ------------------------------------------------------------ mocked Graph
def _mock_social():
    respx.get(f"{GRAPH}/me/accounts").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "page1", "name": "My Page", "access_token": "pagetok",
                  "instagram_business_account": {"id": "ig1"}}]}))
    respx.get(f"{GRAPH}/page1/insights").mock(
        return_value=httpx.Response(200, json=PAGE_INSIGHTS))
    respx.get(f"{GRAPH}/ig1/insights").mock(
        return_value=httpx.Response(200, json=IG_INSIGHTS))


@pytest.fixture
def social_connection(auth_client, db):
    user_id = auth_client.get("/auth/me").json()["id"]
    conn = Connection(user_id=user_id, platform="meta_social", name="social",
                      encrypted_tokens=encrypt_tokens({"access_token": "usertok"}))
    db.add(conn)
    db.commit()
    return conn


@respx.mock
def test_social_full_sync_page_and_ig(auth_client, db, social_connection):
    _mock_social()
    body = {"connection_id": social_connection.id, "date_from": "2026-06-01",
            "date_to": "2026-06-02", "breakdown": "none"}
    r = auth_client.post("/syncs", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "success", r.json()["error"]
    # 2 page-days + 2 ig-days
    assert r.json()["stats"]["rows_inserted"] == 4

    assert db.scalar(select(func.count()).select_from(RawMetaSocial)) == 4
    grains = set(db.scalars(select(FactSocialPerformance.grain).distinct()).all())
    assert grains == {"page", "ig_user"}

    # totals: impressions summed; followers is a snapshot -> max
    r = auth_client.get("/social-metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02"})
    row = r.json()["data"][0]
    assert row["impressions"] == 3300      # 1000+1200+500+600
    assert row["reach"] == 2550            # 800+900+400+450
    assert row["engagement"] == 110        # 50+60 (ig has none)
    assert row["followers"] == 5010        # max, not sum

    # by grain
    r = auth_client.get("/social-metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02", "group_by": ["grain"]})
    by_grain = {d["grain"]: d["impressions"] for d in r.json()["data"]}
    assert by_grain == {"page": 2200, "ig_user": 1100}

    # by date (followers = that day's max across page+ig)
    r = auth_client.get("/social-metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02",
        "group_by": ["date"], "sort": "date"})
    days = r.json()["data"]
    assert [d["date"] for d in days] == ["2026-06-01", "2026-06-02"]
    assert days[0]["impressions"] == 1500 and days[0]["followers"] == 5000

    # idempotent re-sync (return_value mocks persist)
    r2 = auth_client.post("/syncs", json=body)
    assert r2.json()["stats"]["rows_deleted"] == 4
    assert db.scalar(select(func.count()).select_from(FactSocialPerformance)) == 4


@respx.mock
def test_social_watermark_is_dataset_aware(auth_client, db, social_connection):
    from app.services.sync import watermark
    _mock_social()
    auth_client.post("/syncs", json={
        "connection_id": social_connection.id, "date_from": "2026-06-01",
        "date_to": "2026-06-02", "breakdown": "none"})
    # reads fact_social_performance, not the ad table
    assert watermark(db, social_connection.id, "none") == date(2026, 6, 2)
