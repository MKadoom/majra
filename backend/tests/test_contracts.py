"""Contract tests (T7): replay recorded platform responses through the REAL
connectors and check that what comes out honours the normalized contract
(SPEC §2/§3) — and that the ad-level rows add up to the platform's own
account-level totals (the same check the daily reconciliation runs live).

Fixtures live in tests/fixtures/contracts/<platform>/*.json. `synthetic-*`
files are built from documented response shapes; `recorded-*` files come from
`python -m scripts.record_contracts` against the real APIs (redacted). Every
file found is tested, so dropping a new recording in extends coverage.
"""
import json
import pathlib
from datetime import date

import httpx
import pytest
import respx

from app.connectors.base import TokenSet, get_connector
from app.services.reconciliation import within

ROOT = pathlib.Path(__file__).parent / "fixtures" / "contracts"
FIXTURES = sorted(ROOT.glob("*/*.json"))

REQUIRED = {
    "date": str, "spend": float, "impressions": int, "clicks": int,
    "conversions": float, "conversion_value": float, "video_views": int,
    "leads": int, "purchases": int,
}


def _replay(fx: dict):
    exchanges = fx["exchanges"]

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        best = None
        for ex in exchanges:
            r = ex["request"]
            if r["host"] != request.url.host or r["path"] != request.url.path:
                continue
            if all(params.get(k) == v for k, v in r["params"].items()):
                # the most specific recorded match wins
                if best is None or len(r["params"]) > len(best["request"]["params"]):
                    best = ex
        if best is None:
            raise AssertionError(f"no recorded response for {request.url}")
        return httpx.Response(best["status"], json=best["response"])

    respx.route().mock(side_effect=handler)


def _connector(platform):
    c = get_connector(platform)
    if platform == "whatsapp":
        c._cfg.whatsapp_token = "fixture"
    return c


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.name}")
@respx.mock
def test_recorded_responses_honour_the_contract(path):
    fx = json.loads(path.read_text())
    prov = fx["_provenance"]
    platform, day = prov["platform"], date.fromisoformat(prov["date"])
    _replay(fx)
    c = _connector(platform)
    tokens = TokenSet("fixture", None, None,
                      {"waba_ids": [prov["account"]]} if platform == "whatsapp" else {})

    accounts = c.fetch_accounts(tokens)
    acc = next(a for a in accounts if a.external_id == prov["account"])
    assert acc.currency and len(acc.currency) == 3, "accounts must carry a currency"

    rows = [c.normalize_insight(r, "none")
            for r in c.fetch_insights(tokens, acc.external_id, day, day, "none")]
    assert rows, "a recorded day should produce rows"
    for n in rows:
        for key, typ in REQUIRED.items():
            assert key in n, f"{platform}: normalized row missing {key}"
            assert isinstance(n[key], typ), f"{platform}: {key} should be {typ.__name__}"
        assert date.fromisoformat(n["date"]) == day
        for k in ("spend", "impressions", "clicks", "purchases", "conversion_value"):
            assert n[k] >= 0, f"{platform}: negative {k}"
        assert n.get("cost_source", "platform") in (
            "platform", "pricing_analytics", "estimate", "missing")

    totals = c.fetch_account_totals(tokens, acc.external_id, day, day)
    if totals is None:
        return  # this connector doesn't support reconciliation
    t = totals[day.isoformat()]
    for m in ("spend", "impressions", "purchases"):
        assert within(m, t[m], sum(n[m] for n in rows)), (
            f"{platform}: ad-level {m} doesn't add up to the account total")


def test_every_platform_with_reconciliation_has_a_fixture():
    covered = {p.parent.name for p in FIXTURES}
    assert {"meta", "snapchat", "whatsapp"} <= covered
