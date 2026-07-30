"""Saved reports: run a metrics query on a schedule and deliver it somewhere.

A saved report stores a RELATIVE window (`last_days`) rather than fixed dates,
because a report that runs every morning should mean "the last 30 days", not
"the 30 days that were current when I created it".

Three destinations:
  google_sheets — writes the rows into a sheet the user shared with a service
                  account (clear, then write header + rows).
  webhook       — POSTs JSON with an HMAC-SHA256 signature so the receiver can
                  verify it really came from this majra.
  email         — sends the rows as a CSV attachment over SMTP.
"""
import hashlib
import hmac
import json
import smtplib
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import PermanentError
from app.connectors.google_sa import mint_access
from app.core.config import settings
from app.core.observability import REPORTS
from app.models import SavedReport
from app.services import exports
from app.services.datasets import run_dataset_query

log = structlog.get_logger()

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

CADENCE_DELTA = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


# ------------------------------------------------------------------- due?
def is_due(report: SavedReport, now: datetime) -> bool:
    if not report.enabled:
        return False
    delta = CADENCE_DELTA.get(report.cadence)
    if delta is None:
        return False
    if now.hour != report.hour_utc:
        return False
    if report.last_run_at is None:
        return True
    return (now - report.last_run_at) >= (delta - timedelta(minutes=10))


# ------------------------------------------------------------------- run
def window(params: dict, today: date) -> tuple[date, date]:
    """[today - last_days, yesterday] — the report's rolling window."""
    last_days = int(params.get("last_days", 30))
    yesterday = today - timedelta(days=1)
    return yesterday - timedelta(days=last_days - 1), yesterday


def run_report(db: Session, report: SavedReport, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    params = report.params or {}
    date_from, date_to = window(params, now.date())

    rows = run_dataset_query(
        db,
        dataset=report.dataset,
        date_from=date_from,
        date_to=date_to,
        breakdown=params.get("breakdown", "none"),
        platforms=params.get("platform"),
        account_ids=params.get("account_id"),
        group_by=params.get("group_by"),
        sort=params.get("sort"),
        limit=exports.cap(),
        user_id=report.user_id,
        reporting_currency=params.get("currency"),
    )

    # A summary Total row (on by default — most people reading a report want the
    # bottom line). Additive columns summed, ratios recomputed from the sums.
    total = exports.total_row(rows) if params.get("total", True) else None

    kind = (report.destination or {}).get("kind", "unknown")
    try:
        deliver(report, rows, date_from, date_to, total)
        report.last_status = "success"
        report.last_error = None
        REPORTS.labels(kind, "success").inc()
    except Exception as exc:
        report.last_status = "failed"
        report.last_error = f"{type(exc).__name__}: {exc}"
        report.last_run_at = now
        db.commit()
        REPORTS.labels(kind, "failed").inc()
        log.error("report.failed", report_id=report.id, error=str(exc))
        raise
    report.last_run_at = now
    db.commit()
    log.info("report.delivered", report_id=report.id, rows=len(rows))
    return {"rows": len(rows), "date_from": str(date_from), "date_to": str(date_to)}


def run_due_reports(db: Session, now: datetime | None = None) -> list[int]:
    now = now or datetime.now(UTC)
    done: list[int] = []
    for r in db.scalars(select(SavedReport).where(SavedReport.enabled.is_(True))):
        if not is_due(r, now):
            continue
        try:
            run_report(db, r, now=now)
        except Exception:
            pass  # the failure is recorded on the row; other reports still run
        done.append(r.id)
    return done


# -------------------------------------------------------------- delivery
def deliver(
    report: SavedReport,
    rows: list[dict],
    date_from: date,
    date_to: date,
    total: dict | None = None,
):
    dest = report.destination or {}
    kind = dest.get("kind")
    if kind == "google_sheets":
        return deliver_google_sheets(dest, rows, total)
    if kind == "webhook":
        return deliver_webhook(report, dest, rows, date_from, date_to, total)
    if kind == "email":
        return deliver_email(report, dest, rows, date_from, date_to, total)
    if kind in ("slack", "teams"):
        return deliver_chat(report, dest, rows, date_from, date_to, total)
    raise PermanentError(f"unknown report destination '{kind}'")


def _cell_s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _summary_text(
    report: SavedReport,
    rows: list[dict],
    date_from: date,
    date_to: date,
    top: int,
    total: dict | None = None,
) -> str:
    head = (
        f"*{report.name}* — {report.dataset} · {date_from} → {date_to} "
        f"· {len(rows)} rows"
    )
    if not rows:
        return head + "\n(no rows in this window)"
    cols = list(rows[0].keys())[:6]  # keep it readable in a chat message
    lines = [" | ".join(cols)]
    lines += [" | ".join(_cell_s(r.get(c)) for c in cols) for r in rows[:top]]
    if total:  # pin the bottom line, whatever the row count
        lines.append(" | ".join(_cell_s(total.get(c)) for c in cols))
    table = "```\n" + "\n".join(lines) + "\n```"
    more = f"\n…and {len(rows) - top} more" if len(rows) > top else ""
    return head + "\n" + table + more


def deliver_chat(
    report: SavedReport,
    dest: dict,
    rows: list[dict],
    date_from: date,
    date_to: date,
    total: dict | None = None,
) -> None:
    """Slack / Microsoft Teams via an incoming-webhook URL. Both render a simple
    {"text": …} payload, so one path serves both — a compact summary with the
    top rows as a code-block table."""
    url = dest.get("url")
    if not url:
        raise PermanentError(f"{dest.get('kind')} destination needs a webhook url")
    text = _summary_text(report, rows, date_from, date_to, int(dest.get("top", 10)), total)
    res = httpx.post(url, json={"text": text}, timeout=30)
    if res.status_code >= 400:
        raise PermanentError(
            f"{dest.get('kind')} webhook returned {res.status_code}"
        )


def deliver_google_sheets(dest: dict, rows: list[dict], total: dict | None = None) -> None:
    sa = dest.get("service_account")
    sheet_id = dest.get("spreadsheet_id")
    if not sa or not sheet_id:
        raise PermanentError(
            "google_sheets needs a service_account and a spreadsheet_id"
        )
    tab = dest.get("sheet_name") or "majra"
    token = mint_access(sa, SHEETS_SCOPE)
    headers = {"Authorization": f"Bearer {token}"}

    cols = list(rows[0].keys()) if rows else []
    body_rows = rows + [total] if total else rows
    values = [cols] + [[_cell(r.get(c)) for c in cols] for r in body_rows]

    # Clear the tab first, so a shorter result doesn't leave stale rows behind.
    clear = httpx.post(
        f"{SHEETS_API}/{sheet_id}/values/{tab}:clear",
        headers=headers, json={}, timeout=60,
    )
    if clear.status_code >= 400:
        raise PermanentError(f"sheets clear failed: {clear.status_code} {clear.text[:180]}")

    put = httpx.put(
        f"{SHEETS_API}/{sheet_id}/values/{tab}!A1",
        headers=headers,
        params={"valueInputOption": "RAW"},
        json={"values": values},
        timeout=120,
    )
    if put.status_code >= 400:
        raise PermanentError(f"sheets write failed: {put.status_code} {put.text[:180]}")


def _cell(v):
    return "" if v is None else v


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver_webhook(
    report: SavedReport,
    dest: dict,
    rows: list[dict],
    date_from: date,
    date_to: date,
    total: dict | None = None,
) -> None:
    url = dest.get("url")
    if not url:
        raise PermanentError("webhook destination needs a url")
    payload = {
        "report": report.name,
        "dataset": report.dataset,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "currency": (report.params or {}).get("currency"),
        "rows": rows,  # kept clean for machine parsing
        "total": total,  # the summary row, separate so it won't break row loops
    }
    body = json.dumps(payload, default=str).encode()
    headers = {"content-type": "application/json"}
    if dest.get("secret"):
        # The receiver recomputes this to prove the payload really came from us.
        headers["X-Majra-Signature"] = f"sha256={sign(dest['secret'], body)}"
    res = httpx.post(url, content=body, headers=headers, timeout=60)
    if res.status_code >= 400:
        raise PermanentError(f"webhook returned {res.status_code}")


def deliver_email(
    report: SavedReport,
    dest: dict,
    rows: list[dict],
    date_from: date,
    date_to: date,
    total: dict | None = None,
) -> None:
    to = dest.get("to")
    if not to:
        raise PermanentError("email destination needs a `to` address")
    if not settings.smtp_host:
        raise PermanentError("email delivery needs MAJRA_SMTP_HOST to be set")

    ccy = (report.params or {}).get("currency")
    msg = EmailMessage()
    msg["Subject"] = f"{report.name} · {date_from} → {date_to}"
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(
        f"{report.name}\n{len(rows)} rows, {date_from} to {date_to}"
        + (f", in {ccy}" if ccy else "")
        + ".\nThe full table is attached as CSV (with a Total row)."
    )
    csv_rows = rows + [total] if total else rows
    csv_bytes = "".join(exports.stream_csv(csv_rows)).encode()
    msg.add_attachment(
        csv_bytes,
        maintype="text",
        subtype="csv",
        filename=exports.filename(report.dataset, date_from, date_to, "csv"),
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
