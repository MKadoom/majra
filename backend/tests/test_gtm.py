"""GTM connector: end-to-end config-snapshot sync against a mocked Tag Manager
API. Verifies the account→container→workspace→resource walk lands rows in
gtm_resources, that /gtm-resources returns the latest snapshot, and that
re-syncing APPENDS a new snapshot (audit history) rather than replacing."""
import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select

from app.connectors.base import PermanentError
from app.connectors.google_sa import TOKEN_URI
from app.connectors.gtm.connector import API, GTMConnector
from app.models import GtmResource, RawGtmSnapshot

_KEY_PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(serialization.Encoding.PEM,
                   serialization.PrivateFormat.PKCS8,
                   serialization.NoEncryption())
    .decode()
)
SERVICE_ACCOUNT = {
    "type": "service_account",
    "client_email": "majra@project.iam.gserviceaccount.com",
    "private_key": _KEY_PEM,
    "token_uri": TOKEN_URI,
}


def test_gtm_credentials_validation():
    c = GTMConnector()
    with pytest.raises(PermanentError):
        c.credentials_from_input({"service_account": {}})
    tok = c.credentials_from_input({"service_account": SERVICE_ACCOUNT})
    assert tok.extra["service_account"]["client_email"].startswith("majra@")


def _mock_gtm():
    respx.post(TOKEN_URI).mock(return_value=httpx.Response(
        200, json={"access_token": "ya29.gtm", "expires_in": 3600}))
    respx.get(f"{API}/accounts").mock(return_value=httpx.Response(
        200, json={"account": [{"accountId": "6", "name": "GTM Acct"}]}))
    respx.get(f"{API}/accounts/6/containers").mock(return_value=httpx.Response(
        200, json={"container": [{"containerId": "9", "name": "Web",
                                  "publicId": "GTM-XXXX"}]}))
    respx.get(f"{API}/accounts/6/containers/9/workspaces").mock(return_value=httpx.Response(
        200, json={"workspace": [{"workspaceId": "1", "name": "Default Workspace"}]}))
    base = f"{API}/accounts/6/containers/9/workspaces/1"
    respx.get(f"{base}/tags").mock(return_value=httpx.Response(
        200, json={"tag": [{"tagId": "t1", "name": "GA4 Config", "type": "gaawc"}]}))
    respx.get(f"{base}/triggers").mock(return_value=httpx.Response(
        200, json={"trigger": [{"triggerId": "tr1", "name": "All Pages",
                                "type": "pageview"}]}))
    respx.get(f"{base}/variables").mock(return_value=httpx.Response(
        200, json={"variable": [{"variableId": "v1", "name": "Page URL", "type": "u"}]}))


@pytest.fixture
def gtm_connection(auth_client):
    r = auth_client.post("/connections/service-account", json={
        "platform": "gtm", "service_account": SERVICE_ACCOUNT})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@respx.mock
def test_gtm_snapshot_sync_and_history(auth_client, db, gtm_connection):
    _mock_gtm()
    body = {"connection_id": gtm_connection, "date_from": "2026-06-01",
            "date_to": "2026-06-01", "breakdown": "none"}
    r = auth_client.post("/syncs", json=body)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "success", job["error"]
    # container + workspace + tag + trigger + variable = 5 records
    assert job["stats"]["rows_inserted"] == 5
    assert job["stats"]["rows_deleted"] == 0        # append-only

    assert db.scalar(select(func.count()).select_from(GtmResource)) == 5
    assert db.scalar(select(func.count()).select_from(RawGtmSnapshot)) == 5
    kinds = set(db.scalars(select(GtmResource.kind).distinct()).all())
    assert kinds == {"container", "workspace", "tag", "trigger", "variable"}

    # latest snapshot via the read endpoint
    r = auth_client.get("/gtm-resources")
    assert len(r.json()["data"]) == 5
    r = auth_client.get("/gtm-resources", params={"kind": "tag"})
    assert len(r.json()["data"]) == 1
    assert r.json()["data"][0]["name"] == "GA4 Config"

    # re-sync APPENDS a new snapshot (history), not delete+insert
    _mock_gtm()
    r2 = auth_client.post("/syncs", json=body)
    assert r2.json()["stats"]["rows_inserted"] == 5
    assert db.scalar(select(func.count()).select_from(GtmResource)) == 10   # 2 snapshots
    # but the read endpoint still shows just the latest snapshot's 5 rows
    assert len(auth_client.get("/gtm-resources").json()["data"]) == 5


def test_gtm_rejects_oauth_connect(auth_client):
    assert auth_client.get("/connect/gtm").status_code == 400
