"""GA4 connector: normalization + credential validation units, plus an
end-to-end service-account sync against a mocked GA4 Data API (JWT-bearer token
exchange + paginated runReport) landing rows in fact_web_performance."""
import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select

from app.connectors.base import PermanentError
from app.connectors.ga4.connector import DATA_API, GA4Connector
from app.models import Account, Connection, FactWebPerformance, RawGA4Report
from app.services.sync import watermark

TOKEN_URI = "https://oauth2.googleapis.com/token"
PROPERTY = "987654321"

# A real RSA key so pyjwt RS256 signing works (the exchange itself is mocked).
_KEY_PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode()
)
SERVICE_ACCOUNT = {
    "type": "service_account",
    "client_email": "majra@project.iam.gserviceaccount.com",
    "private_key": _KEY_PEM,
    "private_key_id": "abc123",
    "token_uri": TOKEN_URI,
}


# ---------------------------------------------------------------- unit tests
def _ga4_row(yyyymmdd, source, medium, campaign, sessions, users, conv, revenue):
    return {
        "dimensionValues": [
            {"value": yyyymmdd}, {"value": source},
            {"value": medium}, {"value": campaign},
        ],
        "metricValues": [
            {"value": sessions}, {"value": users},
            {"value": conv}, {"value": revenue},
        ],
    }


def test_ga4_normalize_insight():
    n = GA4Connector().normalize_insight(
        _ga4_row("20260601", "google", "cpc", "summer_sale", "120", "100", "5", "250.5"),
        "none",
    )
    assert n["date"] == "2026-06-01"      # YYYYMMDD -> ISO
    assert n["source"] == "google" and n["medium"] == "cpc"
    assert n["campaign_name"] == "summer_sale"
    assert n["sessions"] == 120 and n["users"] == 100
    assert n["conversions"] == 5.0 and n["revenue"] == 250.5


def test_ga4_credentials_validation():
    c = GA4Connector()
    with pytest.raises(PermanentError):
        c.credentials_from_input({"service_account": {}, "property_id": "1"})
    with pytest.raises(PermanentError):
        c.credentials_from_input({"service_account": SERVICE_ACCOUNT})  # no property
    tok = c.credentials_from_input(
        {"service_account": SERVICE_ACCOUNT, "property_id": PROPERTY}
    )
    assert tok.extra["property_id"] == PROPERTY
    assert tok.extra["service_account"]["client_email"].endswith("gserviceaccount.com")


def test_ga4_rejects_oauth_connect(auth_client):
    # GA4 is service-account; the OAuth connect endpoint must refuse it.
    r = auth_client.get("/connect/ga4")
    assert r.status_code == 400 and "service_account" in r.text


# ------------------------------------------------------- mocked GA4 Data API
def _mock_ga4(pages):
    respx.post(TOKEN_URI).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.ga4",
                                               "expires_in": 3600})
    )
    route = respx.post(f"{DATA_API}/properties/{PROPERTY}:runReport")
    route.side_effect = [httpx.Response(200, json=p) for p in pages]


@pytest.fixture
def ga4_connection(auth_client):
    """Create a GA4 connection through the real service-account endpoint."""
    r = auth_client.post("/connections/service-account", json={
        "platform": "ga4",
        "service_account": SERVICE_ACCOUNT,
        "property_id": PROPERTY,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


@respx.mock
def test_ga4_full_sync_and_web_metrics(auth_client, db, ga4_connection):
    # Two report pages to exercise offset pagination (rowCount drives paging).
    page1 = {
        "rows": [
            _ga4_row("20260601", "google", "cpc", "summer", "120", "100", "5", "250.0"),
            _ga4_row("20260601", "(direct)", "(none)", "(not set)", "80", "70", "2", "0"),
        ],
        "rowCount": 3,
    }
    page2 = {
        "rows": [
            _ga4_row("20260602", "google", "cpc", "summer", "60", "55", "3", "150.0"),
        ],
        "rowCount": 3,
    }
    _mock_ga4([page1, page2])

    r = auth_client.post("/syncs", json={
        "connection_id": ga4_connection,
        "date_from": "2026-06-01", "date_to": "2026-06-02", "breakdown": "none",
    })
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "success", job["error"]
    assert job["stats"]["rows_inserted"] == 3

    assert db.scalar(select(func.count()).select_from(FactWebPerformance)) == 3
    assert db.scalar(select(func.count()).select_from(RawGA4Report)) == 3
    # watermark is dataset-aware -> reads the web fact table
    assert watermark(db, ga4_connection, "none").isoformat() == "2026-06-02"

    # web-metrics totals: sessions 260, users 225, conversions 10, revenue 400
    r = auth_client.get("/web-metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02"})
    row = r.json()["data"][0]
    assert row["sessions"] == 260 and row["users"] == 225
    assert row["conversions"] == 10.0 and row["revenue"] == 400.0

    # grouped by source, sorted by sessions desc
    r = auth_client.get("/web-metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02",
        "group_by": ["source"], "sort": "-sessions"})
    data = r.json()["data"]
    by_source = {d["source"]: d["sessions"] for d in data}
    assert by_source == {"google": 180, "(direct)": 80}
    assert data[0]["source"] == "google"   # 180 > 80

    # grouped by date
    r = auth_client.get("/web-metrics", params={
        "date_from": "2026-06-01", "date_to": "2026-06-02",
        "group_by": ["date"], "sort": "date"})
    assert [d["date"] for d in r.json()["data"]] == ["2026-06-01", "2026-06-02"]


@respx.mock
def test_ga4_resync_is_idempotent(auth_client, db, ga4_connection):
    page = {"rows": [
        _ga4_row("20260601", "google", "cpc", "c", "10", "9", "1", "5.0"),
    ], "rowCount": 1}
    _mock_ga4([page])
    body = {"connection_id": ga4_connection, "date_from": "2026-06-01",
            "date_to": "2026-06-01", "breakdown": "none"}
    r1 = auth_client.post("/syncs", json=body)
    assert r1.json()["stats"]["rows_inserted"] == 1

    _mock_ga4([page])  # reset the side_effect for the second run
    r2 = auth_client.post("/syncs", json=body)
    assert r2.json()["stats"]["rows_deleted"] == 1
    assert db.scalar(select(func.count()).select_from(FactWebPerformance)) == 1
    # raw is append-only -> two rows after two syncs
    assert db.scalar(select(func.count()).select_from(RawGA4Report)) == 2
