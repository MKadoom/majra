"""Meta Graph v21 → v26 parity check (T8).

    python -m scripts.meta_parity --connection-id 3

Run in the Railway backend console (needs the stored Meta token). READ-ONLY:
it asks Meta for account-level totals of February 2026 on BOTH versions — v21.0
(still served until 2027-01-21) and v26.0 — sums the connection's accounts
(allow-list respected), and checks both against the reference figures from the
architecture review. Exit code 0 only if v21 == v26 and both match the
reference within a cent / one purchase.
"""
import argparse
import json
import sys
from datetime import date

REFERENCE = {"spend": 86_914.24, "purchases": 1_629, "purchase_value": 177_721.76}
PERIOD = (date(2026, 2, 1), date(2026, 2, 28))


def month_totals(connector, tokens, account_ids, version: str) -> dict:
    """Sum account-level totals for PERIOD on one Graph version."""
    from app.connectors.meta import connector as meta_mod
    from app.connectors.meta import mapping

    base = f"https://graph.facebook.com/{version}"
    total = {"spend": 0.0, "purchases": 0, "purchase_value": 0.0}
    for acc in account_ids:
        params = {
            "level": "account",
            "time_range": json.dumps({"since": PERIOD[0].isoformat(),
                                      "until": PERIOD[1].isoformat()}),
            "fields": "spend,actions,action_values",
            "limit": 50,
        }
        for row in connector._paginate(f"{base}/{acc}/insights", params, tokens):
            total["spend"] += float(row.get("spend") or 0)
            total["purchases"] += int(mapping.action_total(row.get("actions"),
                                                           mapping.PURCHASE))
            total["purchase_value"] += float(mapping.action_total(
                row.get("action_values"), mapping.PURCHASE))
    assert meta_mod  # imported for its HTTP client config
    return {k: round(v, 2) for k, v in total.items()}


def compare(a: dict, b: dict) -> list[str]:
    bad = []
    for k in REFERENCE:
        tol = 1 if k == "purchases" else 0.01
        if abs(a[k] - b[k]) > tol:
            bad.append(f"{k}: {a[k]} vs {b[k]}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--connection-id", type=int, required=True)
    args = ap.parse_args()

    from app.connectors.base import TokenSet
    from app.connectors.meta.connector import MetaConnector
    from app.core.db import SessionLocal
    from app.core.security import decrypt_tokens
    from app.models import Account, Connection

    db = SessionLocal()
    try:
        conn = db.get(Connection, args.connection_id)
        tokens = TokenSet.from_dict(decrypt_tokens(conn.encrypted_tokens))
        wanted = set(conn.synced_account_ids or [])
        accounts = [a.external_id for a in db.query(Account).filter_by(connection_id=conn.id)
                    if not wanted or a.external_id in wanted]
    finally:
        db.close()

    c = MetaConnector()
    v21 = month_totals(c, tokens, accounts, "v21.0")
    v26 = month_totals(c, tokens, accounts, "v26.0")
    print(json.dumps({"accounts": accounts, "v21.0": v21, "v26.0": v26,
                      "reference": REFERENCE}, indent=2))
    problems = [f"v21≠v26 {p}" for p in compare(v21, v26)]
    problems += [f"v26≠reference {p}" for p in compare(v26, REFERENCE)]
    for p in problems:
        print("PARITY FAIL:", p)
    if not problems:
        print("PARITY OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
