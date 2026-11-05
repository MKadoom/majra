"""T5 — metric semantics and SAR as the default currency (SPEC §5a)."""
from datetime import date

import httpx
import pytest
import respx

from app.models import Account, Connection, FactAdPerformance, MetricCoverage
from app.services.metrics import query_metrics


@pytest.fixture
def setup(auth_client, db):
    uid = auth_client.get("/auth/me").json()["id"]
    conn = Connection(user_id=uid, platform="meta", name="M", encrypted_tokens=b"")
    db.add(conn)
    db.flush()
    accs = {}
    for ext, plat, ccy in [("sar", "meta", "SAR"), ("usd", "snapchat", "USD"),
                           ("egp", "tiktok", "EGP"), ("wa", "whatsapp", "SAR")]:
        a = Account(connection_id=conn.id, platform=plat, external_id=ext,
                    name=ext.upper(), currency=ccy)
        db.add(a)
        accs[ext] = a
    db.flush()

    def fact(acc, spend, value=0, purchases=0, conversions=0, sent=0, delivered=0,
             impressions=1000, day=date(2026, 10, 10)):
        return FactAdPerformance(
            date=day, platform=acc.platform, account_id=acc.id, breakdown="none",
            spend=spend, currency=acc.currency, impressions=impressions, clicks=10,
            conversions=conversions, conversion_value=value, video_views=0, leads=0,
            purchases=purchases, messages_sent=sent, messages_delivered=delivered)

    db.add_all([
        fact(accs["sar"], 375, value=750, purchases=5, conversions=9),
        fact(accs["usd"], 100, value=50, purchases=1, conversions=1),
        fact(accs["egp"], 999, value=999, purchases=3),
        fact(accs["wa"], 21, sent=120, delivered=100, impressions=0),
    ])
    db.commit()
    return uid


P = {"date_from": date(2026, 10, 1), "date_to": date(2026, 10, 31)}


def test_default_is_sar_and_unrated_currency_is_excluded_with_warning(db, setup):
    res = query_metrics(db, **P, user_id=setup)
    assert res["currency"] == "SAR"
    row = res["data"][0]
    # 375 SAR + 100 USD*3.75 + 21 SAR; the EGP account has no rate → excluded
    assert row["spend"] == 375 + 375 + 21
    assert res["warnings"] == ["no FX rate for EGP — its spend and value are excluded"]


def test_roas_and_cpa_use_purchases_not_conversions(db, setup):
    row = query_metrics(db, **P, platforms=["meta"], user_id=setup)["data"][0]
    assert row["purchase_value"] == row["conversion_value"] == 750
    assert row["roas"] == 2.0                  # 750 / 375
    assert row["cpa"] == 75.0                  # 375 / 5 purchases (not / 9 conversions)
    assert row["conversions"] == 9             # still reported, platform-native


def test_whatsapp_messages_are_separate_from_purchases(db, setup):
    row = query_metrics(db, **P, platforms=["whatsapp"], user_id=setup)["data"][0]
    assert row["messages_sent"] == 120 and row["messages_delivered"] == 100
    assert row["purchases"] == 0 and row["impressions"] == 0
    assert row["cpa"] is None and row["cpm"] is None


def test_group_by_currency_is_native_and_unconverted(db, setup):
    res = query_metrics(db, **P, group_by=["currency"], user_id=setup)
    assert res["currency"] is None and res["warnings"] == []
    by = {r["currency"]: r["spend"] for r in res["data"]}
    assert by == {"SAR": 396.0, "USD": 100.0, "EGP": 999.0}


def test_explicit_reporting_currency(db, setup):
    res = query_metrics(db, **P, platforms=["meta", "snapchat"],
                        reporting_currency="usd", user_id=setup)
    assert res["currency"] == "USD"
    assert res["data"][0]["spend"] == 200.0    # 375 SAR = 100 USD at the peg


def test_unknown_reporting_currency_raises(db, setup):
    with pytest.raises(ValueError, match="XYZ"):
        query_metrics(db, **P, reporting_currency="XYZ", user_id=setup)


def test_coverage_warning_only_when_range_reaches_before_valid_from(db, setup):
    db.add(MetricCoverage(platform="tiktok", metric="purchases",
                          valid_from=date(2026, 9, 1)))
    db.commit()
    early = query_metrics(db, date_from=date(2026, 8, 1), date_to=date(2026, 9, 30),
                          user_id=setup)
    assert any("tiktok purchases only available from 2026-09-01" in w
               for w in early["warnings"])
    late = query_metrics(db, **P, user_id=setup)
    assert not any("tiktok purchases" in w for w in late["warnings"])
    other = query_metrics(db, date_from=date(2026, 8, 1), date_to=date(2026, 9, 30),
                          platforms=["meta"], user_id=setup)
    assert not any("tiktok" in w for w in other["warnings"])


def test_api_defaults_to_sar_and_422_on_unknown(auth_client, setup):
    params = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
    body = auth_client.get("/metrics", params=params).json()
    assert body["currency"] == "SAR" and "warnings" in body
    assert "purchase_value" in body["data"][0]
    assert auth_client.get("/metrics", params={**params, "currency": "XYZ"}).status_code == 422


def test_funding_defaults_to_sar(auth_client, setup):
    auth_client.post("/card-charges", json={"date": "2026-10-01", "amount": 1000})
    body = auth_client.get("/funding", params={
        "date_from": "2026-10-01", "date_to": "2026-10-31"}).json()
    assert body["currency"] == "SAR"
    assert body["total_charged"] == 1000.0   # the charge defaulted to SAR too


def test_pacing_budget_defaults_to_sar(auth_client, db, setup):
    r = auth_client.post("/budgets", json={"name": "B", "amount_monthly": 1000})
    assert r.json()["currency"] == "SAR"


@respx.mock
def test_fx_refresh_keeps_the_sar_peg(db):
    from app.models import FxRate
    from app.services.fx import refresh_fx_rates

    respx.get("https://fx.test/latest").mock(return_value=httpx.Response(
        200, json={"rates": {"SAR": 3.7512, "EUR": 0.9}}))
    refresh_fx_rates(db, url="https://fx.test/latest")
    rates = {r.currency: float(r.rate_to_usd) for r in db.query(FxRate)}
    assert rates["SAR"] == pytest.approx(1 / 3.75)
    assert rates["EUR"] == pytest.approx(1 / 0.9)


def test_estimated_or_missing_spend_is_warned_and_groupable(db, setup):
    wa = db.query(Account).filter_by(external_id="wa").one()
    db.query(FactAdPerformance).filter_by(account_id=wa.id).update(
        {"cost_source": "estimate"})
    db.add(FactAdPerformance(
        date=date(2026, 10, 11), platform="whatsapp", account_id=wa.id,
        breakdown="none", spend=0, currency="SAR", impressions=0, clicks=0,
        conversions=0, conversion_value=0, video_views=0, leads=0, purchases=0,
        cost_source="missing"))
    db.commit()
    res = query_metrics(db, **P, user_id=setup)
    assert any("whatsapp spend is estimated" in w and "1 day(s)" in w
               for w in res["warnings"])
    assert any("whatsapp spend is unknown on 1 day(s)" in w for w in res["warnings"])
    by = {r["cost_source"]: r["spend"] for r in query_metrics(
        db, **P, platforms=["whatsapp"], group_by=["cost_source"], user_id=setup)["data"]}
    assert by == {"estimate": 21.0, "missing": 0.0}
    clean = query_metrics(db, **P, platforms=["meta"], user_id=setup)
    assert not any("estimated" in w for w in clean["warnings"])
