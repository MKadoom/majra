"""T8 — Meta Graph v26.0."""
import httpx
import respx

from app.connectors.base import TokenSet
from app.connectors.meta.connector import GRAPH, GRAPH_VERSION, MetaConnector
from scripts.meta_parity import REFERENCE, compare, month_totals


def test_all_meta_surfaces_pin_v26():
    from app.connectors.meta_social.connector import GRAPH_VERSION as social
    from app.connectors.whatsapp.config import WhatsAppSettings
    from app.services.alerts import AlertSettings

    assert GRAPH_VERSION == social == "v26.0"
    assert GRAPH.endswith("/v26.0")
    assert WhatsAppSettings().whatsapp_api_version == "v26.0"
    assert AlertSettings().alert_whatsapp_api_version == "v26.0"


def _month(version, spend, purchases, value):
    respx.get(f"https://graph.facebook.com/{version}/act_1/insights").mock(
        return_value=httpx.Response(200, json={"data": [{
            "spend": str(spend),
            "actions": [{"action_type": "omni_purchase", "value": str(purchases)}],
            "action_values": [{"action_type": "omni_purchase", "value": str(value)}],
        }]}))


@respx.mock
def test_parity_check_passes_when_versions_and_reference_agree():
    _month("v21.0", 86914.24, 1629, 177721.76)
    _month("v26.0", 86914.24, 1629, 177721.76)
    c, t = MetaConnector(), TokenSet("t")
    v21 = month_totals(c, t, ["act_1"], "v21.0")
    v26 = month_totals(c, t, ["act_1"], "v26.0")
    assert v26 == REFERENCE
    assert compare(v21, v26) == [] and compare(v26, REFERENCE) == []


@respx.mock
def test_parity_check_reports_a_drift():
    _month("v21.0", 86914.24, 1629, 177721.76)
    _month("v26.0", 86914.24, 1600, 177721.76)
    c, t = MetaConnector(), TokenSet("t")
    drift = compare(month_totals(c, t, ["act_1"], "v21.0"),
                    month_totals(c, t, ["act_1"], "v26.0"))
    assert drift == ["purchases: 1629 vs 1600"]
