"""WhatsApp Business Management connector: normalization units + a mocked
templates→analytics fetch. The connector maps marketing-message metrics onto the
ad_performance shape (delivered→impressions, clicked→clicks, read→conversions).
"""
import httpx
import pytest
import respx

from app.connectors.base import PermanentError
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


def test_whatsapp_normalize_insight():
    n = _c().normalize_insight(
        {
            "template_id": "123", "template_name": "kafalat_7", "start": 1767225600,
            "sent": 1000, "delivered": 950, "read": 300,
            "clicked": [{"type": "url_button", "count": 40}, {"type": "quick_reply", "count": 10}],
        },
        "none",
    )
    assert n["date"] == "2026-01-01"
    assert n["campaign_external_id"] == "123" and n["campaign_name"] == "kafalat_7"
    assert n["impressions"] == 950          # delivered
    assert n["clicks"] == 50                # summed button clicks
    assert n["conversions"] == 300.0        # read
    assert n["purchases"] == 1000           # sent
    assert n["spend"] == 0.0


def test_whatsapp_normalize_scalar_clicked():
    # `clicked` may come back as a scalar rather than a per-button list.
    n = _c().normalize_insight(
        {"template_id": "9", "template_name": "t", "start": 1767225600,
         "delivered": 5, "read": 2, "clicked": 3}, "none"
    )
    assert n["clicks"] == 3 and n["impressions"] == 5


@respx.mock
def test_whatsapp_fetch_insights_maps_templates():
    from datetime import date

    base = "https://graph.facebook.com/v21.0"
    respx.get(f"{base}/WABA1/message_templates").mock(
        return_value=httpx.Response(200, json={
            "data": [{"id": "t1", "name": "kafalat_7"}, {"id": "t2", "name": "salat_0901"}],
            "paging": {},
        })
    )
    respx.get(f"{base}/WABA1/template_analytics").mock(
        return_value=httpx.Response(200, json={"data": [
            {"granularity": "DAILY", "data_points": [
                {"template_id": "t1", "start": 1767225600, "sent": 100, "delivered": 95,
                 "read": 30, "clicked": [{"type": "url_button", "count": 5}]},
                {"template_id": "t2", "start": 1767225600, "sent": 40, "delivered": 38,
                 "read": 10, "clicked": []},
            ]},
        ]})
    )
    from app.connectors.base import TokenSet

    rows = list(_c().fetch_insights(
        TokenSet(access_token="", extra={"waba_ids": ["WABA1"]}),
        "WABA1", date(2026, 1, 1), date(2026, 1, 31), "none",
    ))
    assert len(rows) == 2
    by_name = {r["template_name"]: r for r in rows}
    assert by_name["kafalat_7"]["delivered"] == 95
    assert by_name["salat_0901"]["delivered"] == 38
