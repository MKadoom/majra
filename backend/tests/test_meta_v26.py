"""T8 — Meta Graph v26.0.

The v21.0 vs v26.0 parity check lives in `scripts/meta_parity.py` from #17
(tests in `tests/test_meta_parity.py`).
"""
from app.connectors.meta.connector import GRAPH, GRAPH_VERSION


def test_all_meta_surfaces_pin_v26():
    from app.connectors.meta_social.connector import GRAPH_VERSION as social
    from app.connectors.whatsapp.config import WhatsAppSettings
    from app.services.alerts import AlertSettings

    assert GRAPH_VERSION == social == "v26.0"
    assert GRAPH.endswith("/v26.0")
    assert WhatsAppSettings().whatsapp_api_version == "v26.0"
    assert AlertSettings().alert_whatsapp_api_version == "v26.0"
