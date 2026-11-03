"""Meta Graph API version parity check (T8).

    cd backend
    python -m scripts.meta_parity --connection-id 3
    python -m scripts.meta_parity --connection-id 3 --month 2026-02 --versions v21.0,v26.0

Pulls one closed month for a Meta connection under each Graph version and checks
that the totals are identical, before the connector's pinned version is bumped.

Two paths per version:

1. **Connector path**: `MetaConnector(graph_version=...).fetch_insights` plus
   `normalize_insight`, exactly what a sync runs (ad level, daily, breakdown
   `none`). A renamed field or action type therefore shows up as a changed
   total here.
2. **Account-level cross-check**: one `level=account` insights call per account
   for the whole month (no ad-level paging), normalized by the same mapping.

STRICTLY READ-ONLY. The database session is opened `READ ONLY`. It reads the
connection, its stored token and its accounts, then rolls back. No facts, raw
rows, sync jobs or connection state are written, and the token is never
refreshed (a refresh would hand back a new token that would have to be stored).

Exit codes: 0 when every metric matches across versions on both paths (money to
2 dp, counts exactly) and no account errored; 1 on any mismatch; 2 when the check
could not run (bad arguments, unknown or non-Meta connection, no accounts,
fetch/auth error).
"""
from __future__ import annotations

import argparse
import calendar
import logging
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

DEFAULT_MONTH = "2026-02"
DEFAULT_VERSIONS = ("v21.0", "v26.0")

# Reference totals from the architecture review (T8 acceptance test). Shown for
# information only: they depend on which accounts the connection holds, so they
# do not affect the exit code. Version parity does.
REFERENCE = {
    "2026-02": {
        "spend": Decimal("86914.24"),
        "purchases": 1629,
        "conversion_value": Decimal("177721.76"),
    }
}

MONEY = ("spend", "conversion_value")
METRICS = (
    "spend", "purchases", "conversions", "conversion_value",
    "impressions", "clicks", "rows",
)
LABELS = {
    "spend": "spend",
    "purchases": "purchases",
    "conversions": "conversions",
    "conversion_value": "purchase value",
    "impressions": "impressions",
    "clicks": "clicks",
    "rows": "rows",
}
ACTION_LISTS = ("actions", "action_values", "video_play_actions")
CENT = Decimal("0.01")

log = logging.getLogger("meta_parity")


class SetupError(Exception):
    """The check cannot run (exit 2)."""


# ------------------------------------------------------------------ redaction
_SECRET_PARAMS = re.compile(
    r"((?:access_token|fb_exchange_token|client_secret|appsecret_proof|code)=)[^&\s'\"]+"
)
# Meta user/page tokens start with EAA; catch any that slip through un-keyed.
_META_TOKEN = re.compile(r"\bEAA[A-Za-z0-9]{20,}")


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Strip tokens from anything that may be printed or logged."""
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, "[REDACTED]")
    text = _SECRET_PARAMS.sub(r"\1[REDACTED]", text)
    return _META_TOKEN.sub("[REDACTED]", text)


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self.secrets)
        record.args = None
        return True


# ------------------------------------------------------------------ arguments
def parse_month(value: str) -> tuple[date, date]:
    m = re.fullmatch(r"(\d{4})-(\d{2})", value or "")
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise argparse.ArgumentTypeError(f"month must be YYYY-MM, got {value!r}")
    y, mo = int(m.group(1)), int(m.group(2))
    return date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])


def parse_versions(value: str) -> list[str]:
    from app.connectors.meta.connector import MetaConnector

    versions = [v.strip() for v in (value or "").split(",") if v.strip()]
    if len(versions) < 2 or len(set(versions)) != len(versions):
        raise argparse.ArgumentTypeError(
            "--versions needs at least two distinct versions, e.g. v21.0,v26.0")
    for v in versions:
        try:
            MetaConnector(graph_version=v)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
    return versions


# ------------------------------------------------------------------ totals
def _zero() -> dict:
    return {k: (Decimal(0) if k in MONEY else 0) for k in METRICS}


@dataclass
class PathTotals:
    """Totals of one fetch path under one Graph version."""

    metrics: dict = field(default_factory=_zero)
    per_account: dict = field(default_factory=dict)  # account -> metrics
    fields: set = field(default_factory=set)  # top-level raw keys seen
    # list name ("actions", ...) -> action_type -> summed value
    actions: dict = field(default_factory=lambda: {k: {} for k in ACTION_LISTS})
    errors: dict = field(default_factory=dict)  # account -> redacted error
    out_of_range: int = 0
    api_warnings: list = field(default_factory=list)

    def add(self, account: str, raw: dict, norm: dict) -> None:
        acc = self.per_account.setdefault(account, _zero())
        for target in (self.metrics, acc):
            for k in METRICS:
                if k == "rows":
                    target[k] += 1
                elif k in MONEY:
                    target[k] += Decimal(str(norm[k]))
                else:
                    target[k] += int(norm[k])
        self.fields.update(raw.keys())
        for name in ACTION_LISTS:
            for item in raw.get(name) or []:
                t = item.get("action_type")
                bucket = self.actions[name]
                bucket[t] = bucket.get(t, Decimal(0)) + Decimal(str(item.get("value", 0)))

    def merge(self, other: PathTotals) -> None:
        for k in METRICS:
            self.metrics[k] += other.metrics[k]
        for a, m in other.per_account.items():
            mine = self.per_account.setdefault(a, _zero())
            for k in METRICS:
                mine[k] += m[k]
        self.fields |= other.fields
        for name in ACTION_LISTS:
            for t, v in other.actions[name].items():
                self.actions[name][t] = self.actions[name].get(t, Decimal(0)) + v
        self.errors.update(other.errors)
        self.out_of_range += other.out_of_range


def _accumulate(connector, rows: Iterable[dict], account: str,
                date_from: date, date_to: date) -> PathTotals:
    t = PathTotals()
    for raw in rows:
        norm = connector.normalize_insight(raw, "none")
        day = date.fromisoformat(norm["date"])
        if not date_from <= day <= date_to:
            t.out_of_range += 1  # the sync drops these too
            continue
        t.add(account, raw, norm)
    t.per_account.setdefault(account, _zero())
    return t


def connector_path(connector, tokens, accounts: list[str], date_from: date,
                   date_to: date, secrets: list[str]) -> PathTotals:
    """What a sync would ingest: fetch_insights + normalize_insight per account.
    An account that errors is recorded and excluded, as the sync does."""
    total = PathTotals()
    for acc in accounts:
        try:
            rows = connector.fetch_insights(tokens, acc, date_from, date_to, "none")
            total.merge(_accumulate(connector, rows, acc, date_from, date_to))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            total.errors[acc] = redact(f"{type(exc).__name__}: {exc}", secrets)
    return total


def account_level_path(connector, tokens, accounts: list[str], date_from: date,
                       date_to: date, secrets: list[str]) -> PathTotals:
    """Independent cross-check: one account-level total for the whole month."""
    import json

    params = {
        "level": "account",
        "time_range": json.dumps({"since": date_from.isoformat(),
                                  "until": date_to.isoformat()}),
        "fields": "date_start,spend,impressions,clicks,actions,action_values",
        "limit": 50,
    }
    total = PathTotals()
    for acc in accounts:
        try:
            rows = connector._paginate(f"{connector.graph}/{acc}/insights", params, tokens)
            total.merge(_accumulate(connector, rows, acc, date_from, date_to))
        except Exception as exc:  # noqa: BLE001
            total.errors[acc] = redact(f"{type(exc).__name__}: {exc}", secrets)
    return total


# ------------------------------------------------------------------ compare
def _q(metric: str, value):
    return Decimal(value).quantize(CENT, ROUND_HALF_UP) if metric in MONEY else int(value)


@dataclass
class CompareRow:
    metric: str
    values: list  # one per version, in version order
    diffs: list  # value - baseline (first version), one per version
    ok: bool


def compare(results: dict[str, PathTotals],
            metrics: Iterable[str] = METRICS) -> list[CompareRow]:
    """Compare every version against the first. Money is compared at 2 dp,
    counts exactly."""
    versions = list(results)
    rows = []
    for m in metrics:
        values = [_q(m, results[v].metrics[m]) for v in versions]
        diffs = [v - values[0] for v in values]
        rows.append(CompareRow(m, values, diffs, all(d == 0 for d in diffs)))
    return rows


def account_mismatches(results: dict[str, PathTotals]) -> list[str]:
    """Per-account metrics that differ between versions (for diagnosis)."""
    versions = list(results)
    base = versions[0]
    accounts = sorted({a for r in results.values() for a in r.per_account})
    out = []
    for acc in accounts:
        for m in METRICS:
            vals = [_q(m, results[v].per_account.get(acc, _zero())[m]) for v in versions]
            if any(x != vals[0] for x in vals):
                pairs = ", ".join(f"{v}={_fmt(m, x)}" for v, x in zip(versions, vals, strict=True))
                out.append(f"{acc} {LABELS[m]}: {pairs} (baseline {base})")
    return out


def field_differences(a: PathTotals, b: PathTotals, va: str, vb: str) -> list[str]:
    """Fields and action types present in one version but not the other, and
    action types whose totals moved. A type that vanished in one version while a
    new one appeared with the same total is flagged as a likely rename."""
    out = []
    only_a, only_b = sorted(a.fields - b.fields), sorted(b.fields - a.fields)
    if only_a:
        out.append(f"fields only in {va} (missing in {vb}): {', '.join(only_a)}")
    if only_b:
        out.append(f"fields only in {vb} (new): {', '.join(only_b)}")
    for name in ACTION_LISTS:
        ta, tb = a.actions[name], b.actions[name]
        gone = {t: ta[t] for t in ta.keys() - tb.keys()}
        new = {t: tb[t] for t in tb.keys() - ta.keys()}
        for old_t, val in sorted(gone.items(), key=lambda kv: str(kv[0])):
            twin = next((t for t, v in sorted(new.items(), key=lambda kv: str(kv[0]))
                         if v == val), None)
            if twin is not None:
                out.append(f"{name}: '{old_t}' ({va}) looks renamed to '{twin}' ({vb}), "
                           f"total {val}")
                new.pop(twin)
            else:
                out.append(f"{name}: '{old_t}' missing in {vb} ({va} total {val})")
        for t, val in sorted(new.items(), key=lambda kv: str(kv[0])):
            out.append(f"{name}: '{t}' only in {vb} (total {val})")
        for t in sorted(ta.keys() & tb.keys(), key=str):
            if ta[t] != tb[t]:
                out.append(f"{name}: '{t}' total {ta[t]} ({va}) vs {tb[t]} ({vb})")
    return out


# ------------------------------------------------------------------ rendering
def _fmt(metric: str, value) -> str:
    if metric in MONEY:
        return f"{value:,.2f}"
    return f"{value:,}"


def _fmt_diff(metric: str, d) -> str:
    if d == 0:
        return "0.00" if metric in MONEY else "0"
    return ("+" if d > 0 else "") + _fmt(metric, d)


def render_table(title: str, versions: list[str], rows: list[CompareRow]) -> list[str]:
    head = ["metric", *versions, *[f"diff {v}" for v in versions[1:]], "result"]
    body = [
        [LABELS[r.metric], *[_fmt(r.metric, x) for x in r.values],
         *[_fmt_diff(r.metric, d) for d in r.diffs[1:]], "PASS" if r.ok else "FAIL"]
        for r in rows
    ]
    widths = [max(len(str(c)) for c in col) for col in zip(head, *body, strict=True)]

    def line(cells):
        first = cells[0].ljust(widths[0])
        rest = [str(c).rjust(w) for c, w in zip(cells[1:], widths[1:], strict=True)]
        return "  ".join([first, *rest])

    return [title, line(head), line(["-" * w for w in widths]), *[line(b) for b in body]]


# ------------------------------------------------------------------ database
@dataclass
class ConnectionInfo:
    id: int
    name: str
    status: str
    deleted: bool
    tokens: object  # TokenSet
    accounts: list  # [(external_id, name, currency, platform_label)]
    missing_from_db: list  # allow-listed ids with no norm.accounts row


def load_connection(connection_id: int) -> ConnectionInfo:
    """Read the connection, decrypt its token the way `run_sync` does, and pick
    its accounts (allow-list respected). Read-only transaction, rolled back."""
    from sqlalchemy import select, text

    from app.connectors.base import TokenSet
    from app.core.db import SessionLocal
    from app.core.security import decrypt_tokens
    from app.models import Account, Connection

    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        conn = db.get(Connection, connection_id)
        if conn is None:
            raise SetupError(f"connection {connection_id} not found")
        if conn.platform != "meta":
            raise SetupError(
                f"connection {connection_id} is platform '{conn.platform}', not 'meta'")
        try:
            tokens = TokenSet.from_dict(decrypt_tokens(conn.encrypted_tokens))
        except Exception as exc:  # noqa: BLE001 - message never contains the token
            raise SetupError(f"could not decrypt the stored token ({type(exc).__name__})") from exc
        wanted = set(conn.synced_account_ids or [])
        rows = db.execute(
            select(Account).where(Account.connection_id == conn.id)
            .order_by(Account.external_id)
        ).scalars().all()
        accounts = [(a.external_id, a.name, a.currency, a.platform_override or conn.platform)
                    for a in rows if not wanted or a.external_id in wanted]
        have = {a.external_id for a in rows}
        return ConnectionInfo(
            id=conn.id, name=conn.name, status=conn.status,
            deleted=conn.deleted_at is not None, tokens=tokens, accounts=accounts,
            missing_from_db=sorted(wanted - have),
        )
    finally:
        db.rollback()
        db.close()


# ------------------------------------------------------------------ main
def run(connection_id: int, month: str, versions: list[str],
        out: Callable[[str], None] = print,
        load: Callable[[int], ConnectionInfo] = load_connection,
        connector_factory: Callable | None = None) -> int:
    from app.connectors.meta.connector import MetaConnector

    connector_factory = connector_factory or (lambda v: MetaConnector(graph_version=v))
    date_from, date_to = parse_month(month)
    secrets: list[str] = []

    def say(line: str = "") -> None:
        out(redact(line, secrets))

    try:
        info = load(connection_id)
    except SetupError as exc:
        say(f"ERROR: {exc}")
        return 2
    secrets.extend(s for s in (info.tokens.access_token, info.tokens.refresh_token) if s)
    # Redact anything any library logs while the token is in play.
    handlers = list(logging.getLogger().handlers)
    filt = RedactingFilter(secrets)
    for h in handlers:
        h.addFilter(filt)
    try:
        return _check(info, month, versions, date_from, date_to, secrets, say,
                      connector_factory)
    finally:
        for h in handlers:
            h.removeFilter(filt)


def _check(info: ConnectionInfo, month: str, versions: list[str], date_from: date,
           date_to: date, secrets: list[str], say: Callable[..., None],
           connector_factory: Callable) -> int:
    say(f"Meta parity check: connection {info.id} ({info.name}), "
        f"{date_from.isoformat()}..{date_to.isoformat()}, versions {', '.join(versions)}")
    if info.status != "active":
        say(f"WARNING: connection status is '{info.status}'")
    if info.deleted:
        say("WARNING: connection is soft-deleted")
    if date_to >= date.today():
        say("WARNING: month is not closed yet; totals may move between the two pulls")
    if info.missing_from_db:
        say("WARNING: allow-listed accounts with no norm.accounts row (skipped): "
            + ", ".join(info.missing_from_db))
    if not info.accounts:
        say("ERROR: no accounts for this connection (has it synced at least once?)")
        return 2
    say("Accounts:")
    for ext, name, currency, label in info.accounts:
        say(f"  {ext}  {name}  [{currency or '?'}]  reported as {label}")
    if len({a[2] for a in info.accounts}) > 1:
        say("NOTE: accounts mix currencies; totals below are summed as-is (no FX)")
    ids = [a[0] for a in info.accounts]

    conn_results: dict[str, PathTotals] = {}
    acct_results: dict[str, PathTotals] = {}
    for v in versions:
        c = connector_factory(v)
        conn_results[v] = connector_path(c, info.tokens, ids, date_from, date_to, secrets)
        acct_results[v] = account_level_path(c, info.tokens, ids, date_from, date_to, secrets)
        conn_results[v].api_warnings = sorted(
            redact(w, secrets) for w in getattr(c, "api_warnings", ()) or ())

    errors = [(v, path, acc, err)
              for path, res in (("connector", conn_results), ("account-level", acct_results))
              for v, r in res.items() for acc, err in r.errors.items()]
    auth_failed = any("AuthError" in e[3] for e in errors)

    say()
    conn_rows = compare(conn_results)
    for line in render_table("Connector path (fetch_insights + normalize_insight, ad level, daily)",
                             versions, conn_rows):
        say(line)
    say()
    acct_rows = compare(acct_results, [m for m in METRICS if m != "rows"])
    for line in render_table("Account-level cross-check (level=account, whole month)",
                             versions, acct_rows):
        say(line)

    say()
    say("Connector path vs account-level, per version (informational):")
    for v in versions:
        parts = []
        for m in ("spend", "purchases", "conversion_value", "impressions", "clicks"):
            d = _q(m, conn_results[v].metrics[m]) - _q(m, acct_results[v].metrics[m])
            parts.append(f"{LABELS[m]} {_fmt_diff(m, d)}")
        say(f"  {v}: " + ", ".join(parts))

    ref = REFERENCE.get(month)
    if ref:
        say()
        say(f"Reference for {month} from the architecture review "
            "(informational, not in the exit code):")
        for m, expected in ref.items():
            cells = []
            for v in versions:
                got = _q(m, conn_results[v].metrics[m])
                cells.append(f"{v}={_fmt(m, got)} {'match' if got == expected else 'differs'}")
            say(f"  {LABELS[m]} {_fmt(m, expected)}: " + ", ".join(cells))

    say()
    say("Field / action-type differences (connector path):")
    diffs = []
    base = versions[0]
    for v in versions[1:]:
        diffs += field_differences(conn_results[base], conn_results[v], base, v)
    for line in diffs or ["  none"]:
        say(f"  {line}" if diffs else line)
    mism = account_mismatches(conn_results)
    if mism:
        say()
        say("Per-account differences (connector path):")
        for line in mism:
            say(f"  {line}")
    for v in versions:
        if conn_results[v].out_of_range:
            say(f"NOTE: {v} returned {conn_results[v].out_of_range} row(s) outside the "
                "month (dropped, as the sync does)")
        for w in conn_results[v].api_warnings:
            say(f"API warning ({v}): {w}")

    if errors:
        say()
        say("Errors (these accounts are excluded from the totals above):")
        for v, path, acc, err in errors:
            say(f"  {v} {path} {acc}: {err}")
        if auth_failed:
            say("The stored token was rejected. Reconnect the Meta connection, then re-run.")

    parity = all(r.ok for r in conn_rows + acct_rows)
    say()
    if errors:
        say("RESULT: INCOMPLETE (fetch errors), exit 2")
        return 2
    say("RESULT: PASS, all metrics identical across versions" if parity
        else "RESULT: FAIL, totals differ between versions")
    return 0 if parity else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.meta_parity",
        description="Read-only Meta Graph API version parity check on a closed month.")
    ap.add_argument("--connection-id", type=int, required=True)
    ap.add_argument("--month", default=DEFAULT_MONTH, help="YYYY-MM (default %(default)s)")
    ap.add_argument("--versions", default=",".join(DEFAULT_VERSIONS),
                    help="comma-separated, first is the baseline (default %(default)s)")
    args = ap.parse_args(argv)
    try:
        parse_month(args.month)
        versions = parse_versions(args.versions)
    except argparse.ArgumentTypeError as exc:
        ap.error(str(exc))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO, and Meta URLs carry the access token.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return run(args.connection_id, args.month, versions)


if __name__ == "__main__":
    sys.exit(main())
