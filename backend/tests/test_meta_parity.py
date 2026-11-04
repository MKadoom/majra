"""scripts.meta_parity: the Graph version override on MetaConnector, the
comparison / field-diff logic, redaction, and an end-to-end read-only run
against recorded-shape fixtures (respx, no network)."""
import copy
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.connectors.base import TokenSet
from app.connectors.meta.connector import GRAPH_VERSION, MetaConnector
from app.core.security import encrypt_tokens
from app.models import Account, Connection, FactAdPerformance, RawMetaInsights, SyncJob, User
from scripts import meta_parity as mp

TOKEN = "EAAparitysecrettoken1234567890abcdef"
TOKEN_SET = TokenSet(TOKEN)


def _ad_row(day, ad, spend, purchases="2", value="150.50", ptype="omni_purchase"):
    return {
        "date_start": day, "date_stop": day,
        "campaign_id": "c1", "campaign_name": "Camp",
        "adset_id": "s1", "adset_name": "Set",
        "ad_id": ad, "ad_name": f"Ad {ad}",
        "spend": spend, "impressions": "1000", "clicks": "40",
        "actions": [{"action_type": ptype, "value": purchases},
                    {"action_type": "link_click", "value": "40"}],
        "action_values": [{"action_type": ptype, "value": value}],
    }


AD_ROWS = [
    _ad_row("2026-02-01", "a1", "100.10"),
    _ad_row("2026-02-14", "a2", "200.20", purchases="3", value="300.25"),
    _ad_row("2026-02-28", "a1", "0.01", purchases="0", value="0"),
]


def _account_row(rows):
    return {
        "date_start": "2026-02-01", "date_stop": "2026-02-28",
        "spend": str(sum(Decimal(r["spend"]) for r in rows)),
        "impressions": str(sum(int(r["impressions"]) for r in rows)),
        "clicks": str(sum(int(r["clicks"]) for r in rows)),
        "actions": [{"action_type": "omni_purchase",
                     "value": str(sum(int(r["actions"][0]["value"]) for r in rows))}],
        "action_values": [{"action_type": "omni_purchase",
                           "value": str(sum(Decimal(r["action_values"][0]["value"])
                                            for r in rows))}],
    }


def _mock_graph(by_version: dict[str, list[dict]], seen: list | None = None,
                fail: dict | None = None):
    """Serve ad-level and account-level insights per version. Ad rows come back
    in two pages so paging is exercised too."""
    def handler(request: httpx.Request):
        version = request.url.path.split("/")[1]
        if seen is not None:
            seen.append((version, request.url.params.get("level")))
        if fail and version in fail:
            return httpx.Response(400, json={"error": fail[version]})
        rows = by_version[version]
        if request.url.params.get("level") == "account":
            return httpx.Response(200, json={"data": [_account_row(rows)]})
        if request.url.params.get("after") == "p2":
            return httpx.Response(200, json={"data": rows[2:]})
        nxt = f"{request.url.copy_merge_params({'after': 'p2'})}"
        return httpx.Response(200, json={"data": rows[:2], "paging": {"next": nxt}})

    respx.get(url__regex=r"https://graph\.facebook\.com/v\d+\.\d+/act_1/insights.*") \
        .mock(side_effect=handler)


def _seed(db, platform="meta", allow=None):
    u = User(email="o@x.co", password_hash="x")
    db.add(u)
    db.flush()
    c = Connection(user_id=u.id, platform=platform, name="Main Meta",
                   encrypted_tokens=encrypt_tokens({"access_token": TOKEN}),
                   synced_account_ids=allow)
    db.add(c)
    db.flush()
    db.add(Account(connection_id=c.id, platform=platform, external_id="act_1",
                   name="Acc One", currency="SAR"))
    db.add(Account(connection_id=c.id, platform=platform, external_id="act_2",
                   name="Not allow-listed", currency="SAR"))
    db.commit()
    return c.id


def _write_counts(db):
    db.expire_all()
    return tuple(db.scalar(select(func.count()).select_from(m))
                 for m in (FactAdPerformance, RawMetaInsights, SyncJob))


# ------------------------------------------------------------- version override
def test_default_version_is_the_pinned_constant():
    # Version-agnostic: T8 moved the pin from v21.0 to v26.0.
    c = MetaConnector()
    assert c.graph_version == GRAPH_VERSION
    assert c.graph == f"https://graph.facebook.com/{GRAPH_VERSION}"
    assert c.auth_url("s", "http://cb").startswith(
        f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth?")


def test_version_override_changes_every_url():
    c = MetaConnector(graph_version="v26.0")
    assert c.graph == "https://graph.facebook.com/v26.0"
    assert c.auth_url("s", "http://cb").startswith(
        "https://www.facebook.com/v26.0/dialog/oauth?")
    with respx.mock:
        route = respx.get("https://graph.facebook.com/v26.0/act_1/insights").mock(
            return_value=httpx.Response(200, json={"data": [AD_ROWS[0]]}))
        rows = list(c.fetch_insights(TOKEN_SET, "act_1", date(2026, 2, 1),
                                     date(2026, 2, 28), "none"))
    assert route.called and len(rows) == 1


@pytest.mark.parametrize("bad", ["26.0", "v26", "v26.0/../x", "", "v2 6.0"])
def test_invalid_version_rejected(bad):
    if bad == "":
        assert MetaConnector(graph_version=bad).graph_version == GRAPH_VERSION
        return
    with pytest.raises(ValueError):
        MetaConnector(graph_version=bad)


def test_parse_versions_and_month():
    assert mp.parse_versions("v21.0, v26.0") == ["v21.0", "v26.0"]
    for bad in ("v21.0", "v21.0,v21.0", "v21.0,26"):
        with pytest.raises(mp.argparse.ArgumentTypeError):
            mp.parse_versions(bad)
    assert mp.parse_month("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert mp.parse_month("2024-02")[1] == date(2024, 2, 29)
    for bad in ("2026-13", "2026-2", "Feb"):
        with pytest.raises(mp.argparse.ArgumentTypeError):
            mp.parse_month(bad)


# ------------------------------------------------------------- comparison logic
def _totals(rows):
    c = MetaConnector()
    return mp._accumulate(c, rows, "act_1", date(2026, 2, 1), date(2026, 2, 28))


def test_compare_identical_passes_and_sums_exactly():
    a, b = _totals(AD_ROWS), _totals(copy.deepcopy(AD_ROWS))
    rows = {r.metric: r for r in mp.compare({"v21.0": a, "v26.0": b})}
    assert all(r.ok for r in rows.values())
    assert rows["spend"].values == [Decimal("300.31"), Decimal("300.31")]
    assert rows["purchases"].values == [5, 5]
    assert rows["conversion_value"].values == [Decimal("450.75")] * 2
    assert rows["rows"].values == [3, 3]


def test_compare_flags_a_one_cent_spend_diff():
    changed = copy.deepcopy(AD_ROWS)
    changed[0]["spend"] = "100.11"
    rows = {r.metric: r for r in mp.compare({"v21.0": _totals(AD_ROWS),
                                             "v26.0": _totals(changed)})}
    assert not rows["spend"].ok
    assert rows["spend"].diffs == [Decimal("0.00"), Decimal("0.01")]
    assert rows["impressions"].ok and rows["purchases"].ok


def test_compare_ignores_sub_cent_float_noise_but_not_counts():
    a, b = mp.PathTotals(), mp.PathTotals()
    a.metrics["spend"] = Decimal("0.1") + Decimal("0.2")
    b.metrics["spend"] = Decimal("0.3000001")
    b.metrics["clicks"] = 1
    rows = {r.metric: r for r in mp.compare({"v21.0": a, "v26.0": b})}
    assert rows["spend"].ok
    assert not rows["clicks"].ok and rows["clicks"].diffs == [0, 1]


def test_purchases_are_read_once_and_leads_shown_separately():
    # conversions = purchases + leads in the connector; the check shows the two
    # parts, not their sum, so purchase actions aren't reported twice.
    assert "conversions" not in mp.METRICS
    assert "leads" in mp.METRICS
    with_lead = copy.deepcopy(AD_ROWS)
    with_lead[0]["actions"].append({"action_type": "lead", "value": "4"})
    rows = {r.metric: r for r in mp.compare({"v21.0": _totals(AD_ROWS),
                                             "v26.0": _totals(with_lead)})}
    assert rows["purchases"].ok and rows["purchases"].values == [5, 5]
    assert not rows["leads"].ok and rows["leads"].values == [0, 4]


def test_row_count_difference_fails():
    rows = {r.metric: r for r in mp.compare({"v21.0": _totals(AD_ROWS),
                                             "v26.0": _totals(AD_ROWS[:2])})}
    assert not rows["rows"].ok
    assert rows["rows"].diffs[1] == -1


def test_out_of_range_rows_are_dropped_like_the_sync():
    t = _totals([*AD_ROWS, _ad_row("2026-03-01", "a9", "999")])
    assert t.out_of_range == 1 and t.metrics["rows"] == 3


def test_field_differences_detect_rename_missing_and_changed():
    renamed = copy.deepcopy(AD_ROWS)
    for r in renamed:
        r["actions"][0]["action_type"] = "purchase_v2"
        r["action_values"][0]["action_type"] = "purchase_v2"
        del r["clicks"]
        r["new_field"] = "x"
    renamed[0]["actions"][1]["value"] = "41"
    out = mp.field_differences(_totals(AD_ROWS), _totals(renamed), "v21.0", "v26.0")
    text = "\n".join(out)
    assert "fields only in v21.0 (missing in v26.0): clicks" in text
    assert "fields only in v26.0 (new): new_field" in text
    assert "actions: 'omni_purchase' (v21.0) looks renamed to 'purchase_v2' (v26.0)" in text
    assert "action_values: 'omni_purchase' (v21.0) looks renamed to 'purchase_v2'" in text
    assert "actions: 'link_click' total 120 (v21.0) vs 121 (v26.0)" in text


def test_field_differences_empty_when_identical():
    assert mp.field_differences(_totals(AD_ROWS), _totals(AD_ROWS), "a", "b") == []


def test_account_mismatches_name_the_account():
    a, b = _totals(AD_ROWS), _totals(AD_ROWS[:2])
    out = mp.account_mismatches({"v21.0": a, "v26.0": b})
    assert "act_1 spend: v21.0=300.31, v26.0=300.30 (baseline v21.0)" in out


def test_render_table_marks_pass_and_fail():
    changed = copy.deepcopy(AD_ROWS)
    changed[1]["impressions"] = "999"
    lines = mp.render_table("t", ["v21.0", "v26.0"],
                            mp.compare({"v21.0": _totals(AD_ROWS),
                                        "v26.0": _totals(changed)}))
    imp = next(line for line in lines if line.startswith("impressions"))
    assert imp.split()[-2:] == ["-1", "FAIL"]
    assert next(line for line in lines if line.startswith("spend")).endswith("PASS")


def test_redact():
    msg = (f"GET https://graph.facebook.com/v26.0/x?access_token={TOKEN}&limit=5 "
           "client_secret=shh EAAanotherlooking1234567890tokenvalue")
    out = mp.redact(msg, [TOKEN])
    assert TOKEN not in out and "shh" not in out and "EAAanother" not in out
    assert "limit=5" in out


# ------------------------------------------------------------- end-to-end, read-only
def test_run_passes_when_versions_agree(db, capsys):
    cid = _seed(db, allow=["act_1"])
    before = _write_counts(db)
    seen: list = []
    with respx.mock:
        _mock_graph({"v21.0": AD_ROWS, "v26.0": copy.deepcopy(AD_ROWS)}, seen)
        code = mp.main(["--connection-id", str(cid)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "RESULT: PASS" in out
    assert "act_2" not in out  # allow-list respected
    assert {(v, lvl) for v, lvl in seen} == {
        ("v21.0", "ad"), ("v21.0", "account"), ("v26.0", "ad"), ("v26.0", "account")}
    assert TOKEN not in out
    # strictly read-only: nothing written, connection untouched
    assert _write_counts(db) == before == (0, 0, 0)
    conn = db.get(Connection, cid)
    assert conn.status == "active"


def test_run_fails_and_reports_rename(db, capsys):
    cid = _seed(db, allow=["act_1"])
    v26 = [_ad_row(r["date_start"], r["ad_id"], r["spend"],
                   r["actions"][0]["value"], r["action_values"][0]["value"],
                   ptype="purchase_new_name") for r in AD_ROWS]
    with respx.mock:
        _mock_graph({"v21.0": AD_ROWS, "v26.0": v26})
        code = mp.run(cid, "2026-02", ["v21.0", "v26.0"], out=print)
    out = capsys.readouterr().out
    assert code == 1
    assert "RESULT: FAIL" in out
    assert "looks renamed to 'purchase_new_name'" in out
    purchases = next(line for line in out.splitlines() if line.startswith("purchases"))
    assert purchases.split() == ["purchases", "5", "0", "-5", "FAIL"]
    assert TOKEN not in out


def test_run_auth_error_exits_2_and_redacts(db, capsys):
    cid = _seed(db, allow=["act_1"])
    err = {"code": 190, "message": f"Invalid token {TOKEN}"}
    with respx.mock:
        _mock_graph({"v21.0": AD_ROWS, "v26.0": AD_ROWS}, fail={"v26.0": err})
        code = mp.run(cid, "2026-02", ["v21.0", "v26.0"], out=print)
    out = capsys.readouterr().out
    assert code == 2
    assert "AuthError" in out and "Reconnect the Meta connection" in out
    assert TOKEN not in out and "[REDACTED]" in out
    db.expire_all()
    assert db.get(Connection, cid).status == "active"  # not flipped to needs_reauth


def test_run_rejects_unknown_and_non_meta_connections(db, capsys):
    assert mp.run(999, "2026-02", ["v21.0", "v26.0"], out=print) == 2
    cid = _seed(db, platform="tiktok")
    assert mp.run(cid, "2026-02", ["v21.0", "v26.0"], out=print) == 2
    out = capsys.readouterr().out
    assert "not found" in out and "not 'meta'" in out
