"""Phase 7: read-only API keys, exports, and saved reports.

The important assertions here are the security ones: a key must work on read
endpoints, must NOT work on write endpoints, must die when revoked, and a
report's secrets must never come back out of the API.
"""
import csv
import hashlib
import hmac
import io
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models import Account, Connection, FactAdPerformance, SavedReport
from app.services.reports import is_due, run_due_reports, sign, window

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


@pytest.fixture
def seeded(auth_client, db):
    """Two days of Meta spend to export and report on."""
    user_id = auth_client.get("/auth/me").json()["id"]
    conn = Connection(user_id=user_id, platform="meta", name="m",
                      encrypted_tokens=b"x")
    db.add(conn)
    db.flush()
    acc = Account(connection_id=conn.id, platform="meta", external_id="act_1",
                  name="Main", currency="SAR")
    db.add(acc)
    db.flush()
    db.add_all([
        FactAdPerformance(date=date(2026, 6, 1), platform="meta", account_id=acc.id,
                          breakdown="none", spend=10.5, impressions=1000, clicks=50,
                          conversions=2, conversion_value=100, currency="SAR"),
        FactAdPerformance(date=date(2026, 6, 2), platform="meta", account_id=acc.id,
                          breakdown="none", spend=5.0, impressions=500, clicks=25,
                          conversions=1, conversion_value=50, currency="SAR"),
    ])
    db.commit()
    return acc


# ------------------------------------------------------------------ API keys
def test_api_key_reads_but_cannot_write(client, auth_client, seeded):
    created = auth_client.post("/api-keys", json={"name": "reporting box"})
    assert created.status_code == 201
    body = created.json()
    key = body["key"]
    assert key.startswith("mk_")
    assert body["prefix"] == key[:11]

    # The secret is shown once and never again.
    listed = auth_client.get("/api-keys").json()
    assert "key" not in listed[0]
    assert listed[0]["prefix"] == body["prefix"]

    keyed = {"Authorization": f"Bearer {key}"}

    # READS work.
    r = client.get(
        "/metrics", params={"date_from": "2026-06-01", "date_to": "2026-06-02"},
        headers=keyed,
    )
    assert r.status_code == 200
    assert r.json()["data"][0]["spend"] == 15.5
    assert client.get("/accounts", headers=keyed).status_code == 200

    # WRITES are refused — a key is read-only by construction, because write
    # endpoints don't accept the key principal at all.
    assert client.post(
        "/syncs",
        json={"connection_id": 1, "date_from": "2026-06-01", "date_to": "2026-06-02"},
        headers=keyed,
    ).status_code == 401
    # …and a key cannot mint another key.
    assert client.post("/api-keys", json={"name": "escalate"}, headers=keyed).status_code == 401


def test_api_key_revocation(client, auth_client, seeded):
    key = auth_client.post("/api-keys", json={"name": "temp"}).json()
    keyed = {"Authorization": f"Bearer {key['key']}"}
    assert client.get("/accounts", headers=keyed).status_code == 200

    assert auth_client.delete(f"/api-keys/{key['id']}").status_code == 200
    assert client.get("/accounts", headers=keyed).status_code == 401

    # A made-up key is refused too.
    assert client.get(
        "/accounts", headers={"Authorization": "Bearer mk_not_a_real_key"}
    ).status_code == 401


# ------------------------------------------------------------------- exports
def test_export_csv_json_xlsx_parquet(auth_client, seeded):
    base = {"dataset": "ads", "date_from": "2026-06-01", "date_to": "2026-06-02",
            "group_by": ["date"]}

    csv_res = auth_client.get("/exports", params={**base, "format": "csv"})
    assert csv_res.status_code == 200
    assert "attachment" in csv_res.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(csv_res.text)))
    assert [r["date"] for r in rows] == ["2026-06-01", "2026-06-02"]
    assert float(rows[0]["spend"]) == 10.5

    js = auth_client.get("/exports", params={**base, "format": "json"})
    assert len(js.json()["data"]) == 2

    xl = auth_client.get("/exports", params={**base, "format": "xlsx"})
    assert xl.status_code == 200
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(xl.content))
    sheet = wb.active
    values = list(sheet.values)
    assert values[0][0] == "date"
    assert values[1][0] == "2026-06-01"

    assert auth_client.get("/exports", params={**base, "format": "pdf"}).status_code == 422


def test_export_parquet(auth_client, seeded):
    """Parquet is an optional extra — skip if pyarrow isn't installed."""
    pqlib = pytest.importorskip("pyarrow.parquet")
    r = auth_client.get("/exports", params={
        "dataset": "ads", "date_from": "2026-06-01", "date_to": "2026-06-02",
        "group_by": ["date"], "format": "parquet",
    })
    assert r.status_code == 200
    table = pqlib.read_table(io.BytesIO(r.content))
    assert table.num_rows == 2
    assert "spend" in table.column_names


def test_export_works_with_an_api_key(client, auth_client, seeded):
    key = auth_client.post("/api-keys", json={"name": "nightly"}).json()["key"]
    r = client.get(
        "/exports",
        params={"dataset": "ads", "date_from": "2026-06-01", "date_to": "2026-06-02",
                "format": "csv"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200 and "spend" in r.text


def test_export_is_not_truncated_at_the_page_limit(auth_client, db, seeded):
    """The query services used to clamp page_size to 1000 — an export of 1500
    rows would have silently lost 500 of them."""
    rows = [
        FactAdPerformance(
            date=date(2026, 1, 1) + timedelta(days=i % 300),
            platform="meta", account_id=seeded.id, breakdown="none",
            country=f"C{i}", spend=1, impressions=1, clicks=1, currency="SAR",
        )
        for i in range(1200)
    ]
    db.add_all(rows)
    db.commit()
    r = auth_client.get("/exports", params={
        "dataset": "ads", "date_from": "2026-01-01", "date_to": "2026-12-31",
        "format": "json", "group_by": ["country"],
    })
    assert len(r.json()["data"]) > 1000


# ------------------------------------------------------------ saved reports
def test_report_window_rolls_and_due_logic():
    # A saved report means "the last 30 days", not a range frozen at creation.
    f, t = window({"last_days": 7}, date(2026, 7, 10))
    assert (f, t) == (date(2026, 7, 3), date(2026, 7, 9))

    r = SavedReport(name="x", dataset="ads", cadence="daily", hour_utc=6,
                    enabled=True, params={}, destination={})
    assert is_due(r, datetime(2026, 7, 10, 6, 5, tzinfo=timezone.utc)) is True
    assert is_due(r, datetime(2026, 7, 10, 9, 5, tzinfo=timezone.utc)) is False
    r.last_run_at = datetime(2026, 7, 10, 6, 5, tzinfo=timezone.utc)
    assert is_due(r, datetime(2026, 7, 10, 6, 30, tzinfo=timezone.utc)) is False
    assert is_due(r, datetime(2026, 7, 11, 6, 5, tzinfo=timezone.utc)) is True
    r.enabled = False
    assert is_due(r, datetime(2026, 7, 11, 6, 5, tzinfo=timezone.utc)) is False


@respx.mock
def test_report_delivers_to_webhook_with_hmac(auth_client, db, seeded):
    captured: dict = {}

    def receive(request: httpx.Request):
        captured["body"] = request.content
        captured["sig"] = request.headers.get("X-Majra-Signature")
        return httpx.Response(200, json={"ok": True})

    respx.post("https://hooks.example.com/majra").mock(side_effect=receive)

    created = auth_client.post("/reports", json={
        "name": "Daily spend",
        "dataset": "ads",
        "params": {"last_days": 3650, "group_by": ["platform"]},
        "destination": {"kind": "webhook", "url": "https://hooks.example.com/majra",
                        "secret": "s3cret"},
        "cadence": "daily", "hour_utc": 6,
    })
    assert created.status_code == 201
    # Secrets never come back out of the API.
    assert "secret" not in created.json()["destination"]

    run = auth_client.post(f"/reports/{created.json()['id']}/run")
    assert run.status_code == 200, run.text
    assert run.json()["rows"] == 1

    payload = json.loads(captured["body"])
    assert payload["report"] == "Daily spend"
    assert payload["rows"][0]["platform"] == "meta"
    assert payload["rows"][0]["spend"] == 15.5

    # The receiver can prove the payload came from this majra.
    expected = hmac.new(b"s3cret", captured["body"], hashlib.sha256).hexdigest()
    assert captured["sig"] == f"sha256={expected}"
    assert sign("s3cret", captured["body"]) == expected


@respx.mock
def test_report_delivers_to_google_sheets(auth_client, db, seeded):
    from tests.test_ga4 import SERVICE_ACCOUNT  # a real RSA key, mocked exchange

    respx.post(GOOGLE_TOKEN).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.x", "expires_in": 3600})
    )
    cleared = respx.post(f"{SHEETS}/sheet123/values/majra:clear").mock(
        return_value=httpx.Response(200, json={})
    )
    written = respx.put(f"{SHEETS}/sheet123/values/majra!A1").mock(
        return_value=httpx.Response(200, json={"updatedCells": 6})
    )

    created = auth_client.post("/reports", json={
        "name": "Sheet feed",
        "dataset": "ads",
        "params": {"last_days": 3650, "group_by": ["date"]},
        "destination": {"kind": "google_sheets", "spreadsheet_id": "sheet123",
                        "sheet_name": "majra", "service_account": SERVICE_ACCOUNT},
        "cadence": "daily", "hour_utc": 6,
    }).json()
    # The service-account key is never echoed back.
    assert "service_account" not in created["destination"]

    assert auth_client.post(f"/reports/{created['id']}/run").status_code == 200
    assert cleared.called and written.called

    body = json.loads(written.calls[0].request.content)
    assert body["values"][0][0] == "date"          # header row
    assert body["values"][1][0] == "2026-06-01"    # first data row


def test_report_email_delivery(auth_client, db, seeded, monkeypatch):
    monkeypatch.setenv("MAJRA_SMTP_HOST", "smtp.example.com")
    from app.core import config
    config.settings.smtp_host = "smtp.example.com"

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            sent["host"] = host
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self):
            sent["tls"] = True
        def login(self, u, p):
            pass
        def send_message(self, msg):
            sent["msg"] = msg

    with patch("smtplib.SMTP", FakeSMTP):
        created = auth_client.post("/reports", json={
            "name": "Morning numbers",
            "dataset": "ads",
            "params": {"last_days": 3650, "group_by": ["platform"]},
            "destination": {"kind": "email", "to": "you@example.com"},
            "cadence": "daily", "hour_utc": 6,
        }).json()
        assert auth_client.post(f"/reports/{created['id']}/run").status_code == 200

    config.settings.smtp_host = ""  # restore

    msg = sent["msg"]
    assert msg["To"] == "you@example.com"
    attachment = list(msg.iter_attachments())[0]
    assert attachment.get_filename().endswith(".csv")
    assert b"spend" in attachment.get_payload(decode=True)


@respx.mock
def test_failed_delivery_is_recorded_not_swallowed(auth_client, db, seeded):
    respx.post("https://hooks.example.com/down").mock(
        return_value=httpx.Response(500, json={"error": "nope"})
    )
    created = auth_client.post("/reports", json={
        "name": "Broken", "dataset": "ads",
        "params": {"last_days": 3650},
        "destination": {"kind": "webhook", "url": "https://hooks.example.com/down"},
        "cadence": "daily", "hour_utc": 6,
    }).json()

    assert auth_client.post(f"/reports/{created['id']}/run").status_code == 502

    listed = auth_client.get("/reports").json()[0]
    assert listed["last_status"] == "failed"
    assert "500" in listed["last_error"]


@respx.mock
def test_beat_runs_due_reports_only(auth_client, db, seeded):
    respx.post("https://hooks.example.com/due").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    auth_client.post("/reports", json={
        "name": "6am", "dataset": "ads", "params": {"last_days": 3650},
        "destination": {"kind": "webhook", "url": "https://hooks.example.com/due"},
        "cadence": "daily", "hour_utc": 6,
    })
    auth_client.post("/reports", json={
        "name": "9am", "dataset": "ads", "params": {"last_days": 3650},
        "destination": {"kind": "webhook", "url": "https://hooks.example.com/due"},
        "cadence": "daily", "hour_utc": 9,
    })

    ran = run_due_reports(db, now=datetime(2026, 7, 12, 6, 3, tzinfo=timezone.utc))
    assert len(ran) == 1

    names = {r.name: r.last_status for r in db.scalars(select(SavedReport))}
    assert names["6am"] == "success"
    assert names["9am"] is None  # not its hour
