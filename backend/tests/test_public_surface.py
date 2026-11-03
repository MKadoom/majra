"""T1 — the public surface is closed.

- Sign-up is closed by default (only the first account on an empty DB may
  register, and it becomes the owner).
- A second user sees none of the owner's freshness / pacing / anomalies / GTM
  data, and cannot change the shared FX rates.
- Production hides the API docs and the Prometheus endpoint.
"""
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core import config
from app.core.security import encrypt_tokens
from app.models import Account, Budget, Connection, FactAdPerformance, GtmResource, User

CREDS = {"email": "second@majra.dev", "password": "s3cret-pass"}


def test_first_account_is_owner(auth_client):
    me = auth_client.get("/auth/me").json()
    assert me["is_owner"] is True


def test_registration_closed_once_a_user_exists(auth_client, client):
    r = client.post("/auth/register", json=CREDS)
    assert r.status_code == 403
    assert r.json()["detail"] == "registration is closed"


def test_registration_flag_reopens_signup_without_ownership(
    auth_client, client, monkeypatch
):
    monkeypatch.setattr(config.settings, "allow_registration", True)
    r = client.post("/auth/register", json=CREDS)
    assert r.status_code == 201
    me = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    ).json()
    assert me["is_owner"] is False


@pytest.fixture
def owner_data(auth_client, db):
    """The owner's connection with a stale-able fact history, a budget, an
    anomaly-worthy spike, and a GTM snapshot."""
    owner_id = auth_client.get("/auth/me").json()["id"]
    conn = Connection(user_id=owner_id, platform="meta", name="Owner Meta",
                      encrypted_tokens=encrypt_tokens({"access_token": "x"}))
    gtm = Connection(user_id=owner_id, platform="gtm", name="Owner GTM",
                     encrypted_tokens=encrypt_tokens({"access_token": "x"}))
    db.add_all([conn, gtm])
    db.flush()
    acc = Account(connection_id=conn.id, platform="meta", external_id="act_o",
                  name="Owner Account", currency="USD")
    gacc = Account(connection_id=gtm.id, platform="gtm", external_id="6",
                   name="Owner GTM Acct")
    db.add_all([acc, gacc])
    db.flush()
    yesterday = date.today() - timedelta(days=1)
    for i in range(1, 8):
        db.add(FactAdPerformance(
            date=yesterday - timedelta(days=i), platform="meta", account_id=acc.id,
            breakdown="none", spend=100, currency="USD", impressions=1, clicks=0,
            conversions=0, conversion_value=0, video_views=0, leads=0, purchases=0))
    db.add(FactAdPerformance(
        date=yesterday, platform="meta", account_id=acc.id, breakdown="none",
        spend=900, currency="USD", impressions=1, clicks=0, conversions=0,
        conversion_value=0, video_views=0, leads=0, purchases=0))
    db.add(Budget(user_id=owner_id, name="Owner budget", amount_monthly=1000,
                  currency="USD", enabled=True))
    db.add(GtmResource(account_id=gacc.id, kind="tag", external_id="t1",
                       name="Meta Purchase", type="html",
                       captured_at=datetime.now(UTC), data={}))
    db.commit()
    return owner_id


@pytest.fixture
def second_client(owner_data, client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(config.settings, "allow_registration", True)
    r = client.post("/auth/register", json=CREDS)
    assert r.status_code == 201, r.text
    other = TestClient(app)
    other.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return other


def test_owner_sees_their_reports(auth_client, owner_data):
    assert auth_client.get("/freshness").json()
    assert auth_client.get("/pacing").json()
    assert auth_client.get("/anomalies").json()
    assert auth_client.get("/gtm-resources").json()["data"]


@pytest.mark.parametrize("path", ["/freshness", "/pacing", "/anomalies"])
def test_second_user_sees_no_owner_reports(second_client, path):
    r = second_client.get(path)
    assert r.status_code == 200
    assert r.json() == []


def test_second_user_sees_no_owner_gtm(second_client):
    assert second_client.get("/gtm-resources").json() == {
        "captured_at": None, "data": []
    }


def test_second_user_cannot_write_fx(second_client, auth_client, db):
    assert second_client.put(
        "/fx-rates", json={"currency": "SAR", "rate_to_usd": 1}
    ).status_code == 403
    assert second_client.post("/fx-rates/refresh").status_code == 403
    assert second_client.post("/fx-rates/prune").status_code == 403
    assert second_client.delete("/fx-rates/EUR").status_code == 403
    # reading is still fine, and the owner can still write
    assert second_client.get("/fx-rates").status_code == 200
    assert auth_client.put(
        "/fx-rates", json={"currency": "SAR", "rate_to_usd": 0.2667}
    ).status_code == 200


def test_metrics_endpoint_404_in_production(client, monkeypatch):
    monkeypatch.setattr(config.settings, "environment", "production")
    assert client.get("/_metrics").status_code == 404


def test_docs_served_outside_production(client):
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_docs_hidden_in_production():
    """The docs routes are fixed when the app is built, so build a production
    app in a fresh interpreter rather than reloading this one."""
    code = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        "print([c.get(p).status_code for p in "
        "('/docs', '/redoc', '/openapi.json', '/_metrics')])\n"
    )
    env = {**os.environ, "MAJRA_ENVIRONMENT": "production"}
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)), check=True,
    )
    assert out.stdout.strip().splitlines()[-1] == "[404, 404, 404, 404]"


def test_user_model_defaults_to_not_owner(db):
    u = User(email="x@y.z", password_hash="h")
    db.add(u)
    db.commit()
    db.refresh(u)
    assert u.is_owner is False


def test_metrics_opt_in_for_internal_scraper(client, monkeypatch):
    monkeypatch.setattr(config.settings, "environment", "production")
    monkeypatch.setattr(config.settings, "metrics_in_production", True)
    assert client.get("/_metrics").status_code == 200
