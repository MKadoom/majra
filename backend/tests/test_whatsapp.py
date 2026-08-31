"""WhatsApp Business Management connector: maps account-level messaging
analytics (messages delivered/sent) onto the ad_performance shape
(delivered→impressions, sent→purchases)."""
import httpx
import pytest
import respx

from app.connectors.base import PermanentError, TokenSet
from app.connectors.whatsapp.connector import WhatsAppConnector


def _c() -> WhatsAppConnector:
    c = WhatsAppConnector()
    c._cfg.whatsapp_token = "TESTTOKEN"
    return c


def test_whatsapp_not_configured():
    c = WhatsAppConnector()
    c._cfg.whatsapp_token = ""
    with pytest.raises(PermanentError, match="not configured"):
        c._require_config()


def test_whatsapp_spend_rate_is_configurable():
    c = _c()
    c._cfg.whatsapp_price_per_delivered = 0.30
    n = c.normalize_insight({"waba": "W", "start": 1767225600, "sent": 100, "delivered": 80}, "none")
    assert n["spend"] == 24.0            # 80 * 0.30


def test_whatsapp_normalize_insight():
    n = _c().normalize_insight(
        {"waba": "498954053303405", "start": 1767225600, "sent": 1000, "delivered": 950},
        "none",
    )
    assert n["date"] == "2026-01-01"
    assert n["campaign_external_id"] == "wa-messages-498954053303405"
    assert n["campaign_name"] == "WhatsApp Marketing Messages"
    assert n["impressions"] == 950          # delivered
    assert n["purchases"] == 1000           # sent
    # priced at the default 0.21 SAR / delivered (950 * 0.21)
    assert n["spend"] == 199.5 and n["adset_external_id"] is None


@respx.mock
def test_whatsapp_fetch_insights_reads_messaging_analytics():
    from datetime import date

    base = "https://graph.facebook.com/v21.0"
    respx.get(f"{base}/WABA1").mock(
        return_value=httpx.Response(200, json={
            "id": "WABA1",
            "analytics": {"granularity": "DAY", "data_points": [
                {"start": 1767225600, "end": 1767312000, "sent": 500, "delivered": 480},
                {"start": 1767312000, "end": 1767398400, "sent": 300, "delivered": 295},
            ]},
        })
    )
    rows = list(_c().fetch_insights(
        TokenSet(access_token="", extra={"waba_ids": ["WABA1"]}),
        "WABA1", date(2026, 1, 1), date(2026, 1, 31), "none",
    ))
    assert len(rows) == 2
    assert [r["delivered"] for r in rows] == [480, 295]
    norm = _c().normalize_insight(rows[0], "none")
    assert norm["impressions"] == 480 and norm["campaign_name"] == "WhatsApp Marketing Messages"
