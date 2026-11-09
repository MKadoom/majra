"""Jood order import (ground truth) and claims-vs-truth comparison."""
import base64
import io
from datetime import date, datetime

import pytest

from app.models import Account, Connection, DonationOrder, FactAdPerformance
from app.services import donations as svc

ARABIC_CSV = """رقم الطلب,تاريخ الطلب,المبلغ,حالة الطلب,utm_source,الرابط
1001,2026-10-05 23:30,"١٠٠ ر.س",مكتمل,snapchat,https://t-j.sa/l/abc
1002,2026-10-06 01:15,"250.50",مدفوع,ig,
1003,2026-10-06 10:00,"1,200",ملغي,tiktok,
1004,2026-10-06 11:00,75,مكتمل,,https://t-j.sa/l/tt-777
1005,2026-10-07 09:00,50,مكتمل,newsletter,
,2026-10-07 09:00,50,مكتمل,,
1006,yesterday,50,مكتمل,,
"""


def _import(client, dry_run, **extra):
    return client.post("/donations/import", json={
        "filename": "jood.csv", "content": ARABIC_CSV, "dry_run": dry_run,
        "link_platforms": {"/l/tt-": "tiktok"}, **extra})


# ------------------------------------------------------------- parsing units
def test_amounts_dates_statuses_and_platforms():
    assert svc.parse_amount("١٠٠ ر.س") == 100.0
    assert svc.parse_amount("1,234.50") == 1234.5
    assert svc.parse_amount("1234,50") == 1234.5
    assert svc.parse_amount("SAR 1,200") == 1200.0
    assert svc.parse_amount("abc") is None
    dt = svc.parse_datetime("2026-10-05 23:30")
    assert dt.tzinfo is not None and dt.astimezone(svc.RIYADH).date() == date(2026, 10, 5)
    assert svc.parse_datetime("05/10/2026") == datetime(2026, 10, 5, tzinfo=svc.RIYADH)
    assert svc.parse_datetime("2026-10-05T20:30:00Z").astimezone(svc.RIYADH).day == 5
    assert svc.parse_datetime("nope") is None
    assert svc.is_paid("مكتمل") and svc.is_paid("Completed") and svc.is_paid(None)
    assert not svc.is_paid("ملغي") and not svc.is_paid("refunded")
    assert svc.platform_of("IG") == "meta" and svc.platform_of("Facebook Ads") == "meta"
    assert svc.platform_of("snap") == "snapchat" and svc.platform_of("google") == "google_ads"
    assert svc.platform_of("newsletter") is None


def test_header_detection_and_override():
    m = svc.detect_mapping(["رقم الطلب", "تاريخ الطلب", "المبلغ", "Status"])
    assert m == {"order_id": "رقم الطلب", "ordered_at": "تاريخ الطلب",
                 "amount": "المبلغ", "status": "Status"}
    m2 = svc.detect_mapping(["Ref", "When", "Paid"],
                            {"order_id": "Ref", "ordered_at": "When", "amount": "Paid"})
    assert m2["order_id"] == "Ref"
    with pytest.raises(ValueError, match="not in the file"):
        svc.detect_mapping(["a"], {"order_id": "b"})


def test_missing_required_columns_are_explained(auth_client):
    r = auth_client.post("/donations/import", json={
        "filename": "x.csv", "content": "foo,bar\n1,2\n"})
    assert r.status_code == 422
    assert "order_id" in r.json()["detail"] and "mapping" in r.json()["detail"]


# --------------------------------------------------------------- import flow
def test_dry_run_previews_without_writing(auth_client, db):
    body = _import(auth_client, dry_run=True).json()
    assert body["dry_run"] is True and body["inserted"] == 0
    assert body["mapping"]["order_id"] == "رقم الطلب"
    assert body["rows_read"] == 7 and body["valid"] == 5 and body["paid"] == 4
    assert {s["reason"] for s in body["skipped"]} == {"no order id", "unreadable date"}
    assert body["statuses"]["ملغي"] == 1
    assert body["by_platform"] == {
        "snapchat": {"orders": 1, "amount": 100.0},
        "meta": {"orders": 1, "amount": 250.5},
        "tiktok": {"orders": 1, "amount": 75.0},       # from the Jood per-platform link
        "unattributed": {"orders": 1, "amount": 50.0},
    }
    assert body["date_range"] == ["2026-10-05", "2026-10-07"]
    assert db.query(DonationOrder).count() == 0


def test_import_is_idempotent_and_updates_in_place(auth_client, db):
    first = _import(auth_client, dry_run=False).json()
    assert (first["inserted"], first["updated"]) == (5, 0)
    again = _import(auth_client, dry_run=False).json()
    assert (again["inserted"], again["updated"]) == (0, 5)
    assert db.query(DonationOrder).count() == 5
    o = db.query(DonationOrder).filter_by(external_id="1001").one()
    # 23:30 Riyadh on Oct 5 stays Oct 5 (not the UTC day)
    assert o.date == date(2026, 10, 5) and float(o.amount) == 100.0
    assert o.platform == "snapchat" and o.currency == "SAR" and o.is_paid
    cancelled = db.query(DonationOrder).filter_by(external_id="1003").one()
    assert cancelled.is_paid is False


def test_orders_are_per_user(auth_client, client, db, monkeypatch):
    from app.core import config

    _import(auth_client, dry_run=False)
    monkeypatch.setattr(config.settings, "allow_registration", True)
    tok = client.post("/auth/register", json={
        "email": "other@x.co", "password": "longenough"}).json()["access_token"]
    other = {"Authorization": f"Bearer {tok}"}
    r = client.get("/donations", params={"date_from": "2026-10-01",
                                         "date_to": "2026-10-31"}, headers=other)
    assert r.json()["by_platform"] == {}


def test_xlsx_import(auth_client, db):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Order ID", "Created At", "Total", "Status", "Platform"])
    ws.append([2001, datetime(2026, 10, 8, 14, 0), 300, "completed", "Snapchat"])
    ws.append([2002, datetime(2026, 10, 8, 15, 0), 120.5, "paid", "TikTok"])
    buf = io.BytesIO()
    wb.save(buf)
    r = auth_client.post("/donations/import", json={
        "filename": "orders.xlsx", "dry_run": False,
        "content_base64": base64.b64encode(buf.getvalue()).decode()})
    assert r.status_code == 200, r.text
    assert r.json()["inserted"] == 2
    o = db.query(DonationOrder).filter_by(external_id="2001").one()
    assert o.platform == "snapchat" and float(o.amount) == 300.0


def test_xlsx_needs_base64(auth_client):
    r = auth_client.post("/donations/import", json={"filename": "a.xlsx", "content": "x"})
    assert r.status_code == 422


def test_bad_link_platform_rejected(auth_client):
    r = _import(auth_client, dry_run=True, link_platforms={"/l/": "myspace"})
    assert r.status_code == 422


# ---------------------------------------------------------------- comparison
@pytest.fixture
def claims(auth_client, db):
    uid = auth_client.get("/auth/me").json()["id"]
    conn = Connection(user_id=uid, platform="snapchat", name="S", encrypted_tokens=b"")
    db.add(conn)
    db.flush()
    acc = Account(connection_id=conn.id, platform="snapchat", external_id="sa1",
                  name="Snap", currency="SAR")
    db.add(acc)
    db.flush()
    db.add(FactAdPerformance(date=date(2026, 10, 6), platform="snapchat", account_id=acc.id,
                             breakdown="none", spend=400, currency="SAR", impressions=10,
                             clicks=1, conversions=3, conversion_value=500, video_views=0,
                             leads=0, purchases=3))
    db.commit()
    return uid


def test_compare_claims_with_jood(auth_client, db, claims):
    _import(auth_client, dry_run=False)
    r = auth_client.get("/donations/compare", params={
        "date_from": "2026-10-01", "date_to": "2026-10-31"})
    body = r.json()
    rows = {x["platform"]: x for x in body["rows"]}
    snap = rows["snapchat"]
    assert snap["claimed_purchases"] == 3 and snap["jood_orders"] == 1
    assert snap["claim_ratio"] == 3.0            # Snapchat claims 3x the real donations
    assert snap["cost_per_jood_donation"] == 400.0
    assert snap["jood_roas"] == 0.25             # 100 SAR real / 400 SAR spend
    assert rows["meta"]["claimed_purchases"] == 0 and rows["meta"]["jood_orders"] == 1
    t = body["totals"]
    assert t["jood_orders"] == 4 and t["jood_amount"] == 475.5
    assert t["jood_unattributed_orders"] == 1
    assert t["claimed_purchases_all_platforms"] == 3
    assert body["currency"] == "SAR"


def test_compare_without_imports_warns(auth_client, claims):
    body = auth_client.get("/donations/compare", params={
        "date_from": "2026-10-01", "date_to": "2026-10-31"}).json()
    assert any("no Jood orders imported" in w for w in body["warnings"])


def test_orders_listing_and_api_key_read(auth_client, db):
    _import(auth_client, dry_run=False)
    key = auth_client.post("/api-keys", json={"name": "mcp"}).json()["key"]
    r = auth_client.get("/donations/orders", params={
        "date_from": "2026-10-01", "date_to": "2026-10-31", "platform": "meta"},
        headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert [o["order_id"] for o in r.json()] == ["1002"]
    # a read-only key can't import
    w = auth_client.post("/donations/import", json={"filename": "a.csv", "content": "x"},
                         headers={"Authorization": f"Bearer {key}"})
    assert w.status_code == 401
