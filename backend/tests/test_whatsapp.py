"""WhatsApp Business Management connector (T6).

- messaging analytics → messages_sent / messages_delivered (never purchases or
  impressions, SPEC §5a)
- pricing_analytics → spend with cost_source="pricing_analytics"
- Riyadh-day buckets: half-hour points summed per Asia/Riyadh date
- no cost from Meta → an estimate, marked cost_source="estimate" (or "missing"
  if the configured price is in another currency)
"""
from datetime import date, datetime, timedelta

import httpx
import pytest
import respx

from app.connectors.base import PermanentError, TokenSet
from app.connectors.whatsapp.connector import (
    RIYADH,
    WhatsAppConnector,
    riyadh_day,
    riyadh_midnight_ts,
)

BASE = "https://graph.facebook.com/v21.0"


def _c() -> WhatsAppConnector:
    c = WhatsAppConnector()
    c._cfg.whatsapp_token = "TESTTOKEN"
    c._currency["WABA1"] = "SAR"
    return c


def _ts(y, m, d, hh=0, mm=0) -> int:
    """A Riyadh wall-clock time as a unix timestamp."""
    return int(datetime(y, m, d, hh, mm, tzinfo=RIYADH).timestamp())


def _mock(analytics_points, pricing_points, pricing_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        fields = request.url.params.get("fields", "")
        if fields.startswith("pricing_analytics"):
            if pricing_status != 200:
                return httpx.Response(pricing_status, json={"error": {"code": 100,
                                      "message": "granularity not supported"}})
            return httpx.Response(200, json={"pricing_analytics": {
                "data": [{"data_points": pricing_points}]}})
        return httpx.Response(200, json={"analytics": {"data_points": analytics_points}})
    return respx.get(f"{BASE}/WABA1").mock(side_effect=handler)


def _fetch(c, d1, d2):
    return list(c.fetch_insights(TokenSet("", extra={"waba_ids": ["WABA1"]}),
                                 "WABA1", d1, d2, "none"))


def test_whatsapp_not_configured():
    c = WhatsAppConnector()
    c._cfg.whatsapp_token = ""
    with pytest.raises(PermanentError, match="not configured"):
        c._require_config()


def test_riyadh_helpers():
    # 00:00 Riyadh on Jan 1 is 21:00 UTC on Dec 31
    assert riyadh_midnight_ts(date(2026, 1, 1)) == 1767214800
    # 22:30 UTC Dec 31 is already Jan 1 in Riyadh
    assert riyadh_day(1767220200) == date(2026, 1, 1)


@respx.mock
def test_real_cost_summed_per_riyadh_day():
    route = _mock(
        analytics_points=[
            {"start": _ts(2026, 1, 1, 0, 0), "sent": 100, "delivered": 90},
            {"start": _ts(2026, 1, 1, 23, 30), "sent": 10, "delivered": 10},
            {"start": _ts(2026, 1, 2, 0, 0), "sent": 5, "delivered": 5},
        ],
        pricing_points=[
            {"start": _ts(2026, 1, 1, 0, 0), "pricing_category": "MARKETING",
             "pricing_type": "REGULAR", "volume": 90, "cost": 18.9},
            {"start": _ts(2026, 1, 1, 23, 30), "pricing_category": "MARKETING",
             "pricing_type": "REGULAR", "volume": 10, "cost": 2.1},
            {"start": _ts(2026, 1, 1, 12, 0), "pricing_category": "SERVICE",
             "pricing_type": "FREE_CUSTOMER_SERVICE", "volume": 3, "cost": 0},
            {"start": _ts(2026, 1, 2, 0, 0), "pricing_category": "UTILITY",
             "pricing_type": "REGULAR", "volume": 5, "cost": 0.5},
        ],
    )
    rows = _fetch(_c(), date(2026, 1, 1), date(2026, 1, 2))
    assert [(r["day"], r["sent"], r["delivered"], round(r["cost"], 4)) for r in rows] == [
        ("2026-01-01", 110, 100, 21.0),
        ("2026-01-02", 5, 5, 0.5),
    ]
    # the window is aligned to Riyadh midnight and asks for half-hour points
    fields = [c.request.url.params["fields"] for c in route.calls]
    start, end = riyadh_midnight_ts(date(2026, 1, 1)), riyadh_midnight_ts(date(2026, 1, 3))
    assert f"analytics.start({start}).end({end}).granularity(HALF_HOUR)" in fields
    assert any(f.startswith(f"pricing_analytics.start({start}).end({end})"
                            ".granularity(HALF_HOUR)") for f in fields)

    n = _c().normalize_insight(rows[0], "none")
    assert n["spend"] == 21.0 and n["cost_source"] == "pricing_analytics"
    assert n["date"] == "2026-01-01"
    assert n["messages_sent"] == 110 and n["messages_delivered"] == 100
    assert n["purchases"] == 0 and n["impressions"] == 0
    assert n["campaign_external_id"] == "wa-messages-WABA1"


@respx.mock
def test_falls_back_to_daily_when_half_hour_is_refused():
    calls = []

    def handler(request):
        f = request.url.params.get("fields", "")
        calls.append(f)
        if "HALF_HOUR" in f:
            return httpx.Response(400, json={"error": {"code": 100, "message": "no"}})
        if f.startswith("pricing_analytics"):
            return httpx.Response(200, json={"pricing_analytics": {"data": [{
                "data_points": [{"start": _ts(2026, 1, 1, 3), "cost": 7.0}]}]}})
        return httpx.Response(200, json={"analytics": {"data_points": [
            {"start": _ts(2026, 1, 1, 3), "sent": 50, "delivered": 40}]}})

    respx.get(f"{BASE}/WABA1").mock(side_effect=handler)
    rows = _fetch(_c(), date(2026, 1, 1), date(2026, 1, 1))
    assert rows[0]["granularity"] == "DAILY" and rows[0]["cost"] == 7.0
    assert any("granularity(DAY)" in f for f in calls)


@respx.mock
def test_no_cost_from_meta_is_an_estimate_and_marked():
    _mock(analytics_points=[{"start": _ts(2026, 1, 1), "sent": 1000, "delivered": 950}],
          pricing_points=[])
    rows = _fetch(_c(), date(2026, 1, 1), date(2026, 1, 1))
    assert rows[0]["cost"] is None
    n = _c().normalize_insight(rows[0], "none")
    assert n["cost_source"] == "estimate"
    assert n["spend"] == 199.5           # 950 × 0.21 SAR


def test_estimate_rate_is_configurable():
    c = _c()
    c._cfg.whatsapp_price_per_delivered = 0.30
    n = c.normalize_insight({"waba": "WABA1", "day": "2026-01-01", "sent": 100,
                             "delivered": 80, "cost": None, "currency": "SAR"}, "none")
    assert n["spend"] == 24.0 and n["cost_source"] == "estimate"


def test_no_estimate_in_a_foreign_currency():
    n = _c().normalize_insight({"waba": "WABA1", "day": "2026-01-01", "sent": 100,
                                "delivered": 80, "cost": None, "currency": "USD"}, "none")
    assert n["spend"] == 0.0 and n["cost_source"] == "missing"


@respx.mock
def test_account_currency_comes_from_the_waba():
    respx.get(f"{BASE}/WABA1").mock(return_value=httpx.Response(
        200, json={"id": "WABA1", "name": "Althuraya", "currency": "usd"}))
    c = _c()
    accs = c.fetch_accounts(TokenSet("", extra={"waba_ids": ["WABA1"]}))
    assert accs[0].currency == "USD" and c._currency["WABA1"] == "USD"


@respx.mock
def test_days_without_any_data_are_not_emitted():
    _mock(analytics_points=[{"start": _ts(2026, 1, 2), "sent": 1, "delivered": 1}],
          pricing_points=[])
    rows = _fetch(_c(), date(2026, 1, 1), date(2026, 1, 3))
    assert [r["day"] for r in rows] == ["2026-01-02"]


def test_breakdowns_other_than_none_are_refused():
    with pytest.raises(PermanentError):
        list(_c().fetch_insights(TokenSet(""), "WABA1", date(2026, 1, 1),
                                 date(2026, 1, 1) + timedelta(days=1), "country"))
