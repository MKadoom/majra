"""Tracking changes (SPEC §5a): /metrics warns when a range straddles a day the
meaning of a tracked metric changed."""
from datetime import date

from app.models import TrackingChange
from app.services.metrics import query_metrics


def _seed(db):
    db.add_all([
        TrackingChange(changed_on=date(2026, 11, 9), platform="google_ads",
                       metric="purchases", note="deduped by transaction ID"),
        TrackingChange(changed_on=date(2026, 11, 9), platform="tiktok",
                       metric="purchases", note="ad-link visits only"),
        TrackingChange(changed_on=date(2026, 6, 1), platform=None,
                       metric="purchases", note="consent banner"),
    ])
    db.commit()


def _hits(res, needle):
    return [w for w in res["warnings"] if needle in w]


def test_warns_only_when_range_straddles_the_change(db):
    _seed(db)
    across = query_metrics(db, date_from=date(2026, 11, 1), date_to=date(2026, 11, 15))
    assert _hits(across, "google_ads purchases tracking changed on 2026-11-09")
    assert _hits(across, "tiktok purchases tracking changed on 2026-11-09")
    # starting on the change day: everything in range is after it
    after = query_metrics(db, date_from=date(2026, 11, 9), date_to=date(2026, 11, 15))
    before = query_metrics(db, date_from=date(2026, 11, 1), date_to=date(2026, 11, 8))
    assert not _hits(after, "tracking changed") and not _hits(before, "tracking changed")


def test_platform_filter_and_all_platform_changes(db):
    _seed(db)
    meta_only = query_metrics(db, date_from=date(2026, 5, 1), date_to=date(2026, 11, 15),
                              platforms=["meta"])
    assert not _hits(meta_only, "google_ads") and not _hits(meta_only, "tiktok")
    assert _hits(meta_only, "all platforms purchases tracking changed on 2026-06-01")


def test_api_list_add_delete(auth_client, db):
    _seed(db)
    listed = auth_client.get("/tracking-changes").json()
    assert [c["changed_on"] for c in listed][:2] == ["2026-11-09", "2026-11-09"]
    r = auth_client.post("/tracking-changes", json={
        "changed_on": "2026-11-20", "platform": "meta", "note": "CAPI token rotated"})
    assert r.status_code == 201 and r.json()["metric"] == "purchases"
    cid = r.json()["id"]
    body = auth_client.get("/metrics", params={
        "date_from": "2026-11-15", "date_to": "2026-11-25"}).json()
    assert any("meta purchases tracking changed on 2026-11-20" in w for w in body["warnings"])
    assert auth_client.delete(f"/tracking-changes/{cid}").status_code == 204
    assert auth_client.delete(f"/tracking-changes/{cid}").status_code == 404


def test_writes_are_owner_only(client, auth_client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core import config
    from app.main import app

    monkeypatch.setattr(config.settings, "allow_registration", True)
    r = client.post("/auth/register", json={"email": "second@majra.dev",
                                            "password": "s3cret-pass"})
    assert r.status_code == 201, r.text
    other = TestClient(app)
    other.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    assert other.post("/tracking-changes", json={
        "changed_on": "2026-11-20", "note": "nope"}).status_code == 403
    assert other.delete("/tracking-changes/1").status_code == 403
    assert other.get("/tracking-changes").status_code == 200
