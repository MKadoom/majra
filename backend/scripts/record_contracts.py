"""Record real platform responses as contract fixtures (T7).

    python -m scripts.record_contracts --connection-id 3 --date 2026-10-15

Run it where the database and platform credentials live (the Railway backend
service console). It is READ-ONLY against the platforms: it calls the same
endpoints a one-day sync and a reconciliation make, for ONE day, and saves every
JSON response under tests/fixtures/contracts/<platform>/recorded-*.json, then
prints them so they can be copied out.

Redaction before anything is written: access tokens, app secrets and any
`*token*` / `*secret*` query parameter are dropped from URLs; names (account,
campaign, ad set, ad, organization) become "redacted"; numbers, ids, dates and
currencies are kept because the contract tests need them.
"""
import argparse
import json
import pathlib
import re
from datetime import date

import httpx

OUT = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "contracts"
NAME_KEYS = re.compile(r"(^|_)name$")
SECRET_PARAM = re.compile(r"token|secret|password|key", re.I)


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("redacted" if NAME_KEYS.search(k) and isinstance(v, str)
                    else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def clean_request(req: httpx.Request) -> dict:
    params = {k: v for k, v in req.url.params.multi_items()
              if not SECRET_PARAM.search(k)}
    return {"method": req.method, "host": req.url.host, "path": req.url.path,
            "params": params}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--connection-id", type=int, required=True)
    ap.add_argument("--date", type=date.fromisoformat, required=True)
    ap.add_argument("--account", help="external id of ONE account (default: first)")
    args = ap.parse_args()

    from app.connectors.base import TokenSet, get_connector
    from app.core.db import SessionLocal
    from app.core.security import decrypt_tokens
    from app.models import Connection

    captured: list[dict] = []
    original = httpx.HTTPTransport.handle_request

    def recording(self, request):
        resp = original(self, request)
        resp.read()
        try:
            body = resp.json()
        except ValueError:
            body = None
        if body is not None and "oauth" not in request.url.path:
            captured.append({"request": clean_request(request),
                             "status": resp.status_code, "response": redact(body)})
        return resp

    httpx.HTTPTransport.handle_request = recording
    db = SessionLocal()
    try:
        conn = db.get(Connection, args.connection_id)
        c = get_connector(conn.platform)
        tokens = TokenSet.from_dict(decrypt_tokens(conn.encrypted_tokens))
        accounts = c.fetch_accounts(tokens)
        acc = next(a for a in accounts if not args.account or a.external_id == args.account)
        list(c.fetch_insights(tokens, acc.external_id, args.date, args.date, "none"))
        c.fetch_account_totals(tokens, acc.external_id, args.date, args.date)
    finally:
        httpx.HTTPTransport.handle_request = original
        db.close()

    folder = OUT / conn.platform
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"recorded-{args.date.isoformat()}.json"
    path.write_text(json.dumps({
        "_provenance": {"kind": "recorded", "platform": conn.platform,
                        "date": args.date.isoformat(), "account": acc.external_id},
        "exchanges": captured,
    }, indent=1, ensure_ascii=False))
    print(path)
    print(path.read_text())


if __name__ == "__main__":
    main()
