"""Operator alerts.

One place that turns "something needs a human" into a message. Two channels,
either or both:

- **WhatsApp** (Meta WhatsApp Cloud API) — the channel the operator reads
  daily. Configured with MAJRA_ALERT_WHATSAPP_TOKEN, _PHONE_NUMBER_ID and _TO.
  Business-initiated messages outside WhatsApp's 24-hour customer-service
  window must use an approved template, so set MAJRA_ALERT_WHATSAPP_TEMPLATE
  (a template with ONE body variable, {{1}}); without it a plain text message
  is sent, which WhatsApp only delivers inside that window.
- **Webhook** (MAJRA_ALERT_WEBHOOK_URL) — the original JSON POST, kept for
  backward compatibility: ``{"event": <kind>, ...payload}``.

Alert kinds raised here:
  sync.failed       a sync job failed for good (after retries)
  accounts.skipped  a sync finished but some ad accounts errored and were skipped
  token.expiring    a platform token expires within TOKEN_WARN_DAYS (or already has)
  api.version       a platform API version is near sunset / the API warned about it
  data.stale / budget.over / spend.anomaly   the existing daily sweeps
  digest.daily      the morning summary

Sending never raises: an alert channel being down must not fail a sync. Secrets
(the WhatsApp token) are never logged; failures log the channel and HTTP status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx
import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings as core_settings
from app.core.security import decrypt_tokens

log = structlog.get_logger()

TOKEN_WARN_DAYS = 14
API_VERSION_WARN_DAYS = 90

# Published sunset dates for API versions majra pins. Add the next one when a
# connector is upgraded. Meta Graph v21.0 is retired 2027-01-21 (majra moved to
# v26.0 in T8; Meta hasn't published v26.0's sunset yet — add it when it does).
API_SUNSETS: dict[str, dict[str, date]] = {
    "meta": {"v21.0": date(2027, 1, 21)},
}

WHATSAPP_MAX_CHARS = 1000  # template body params are capped (~1024); stay under


class AlertSettings(BaseSettings):
    """Alert channel settings (kept here so core/config.py stays untouched)."""

    model_config = SettingsConfigDict(env_prefix="MAJRA_", env_file=".env", extra="ignore")

    alert_whatsapp_token: str = ""
    alert_whatsapp_phone_number_id: str = ""
    # Comma-separated recipient numbers in international format, digits only
    # (e.g. 9665XXXXXXXX).
    alert_whatsapp_to: str = ""
    alert_whatsapp_template: str = ""
    alert_whatsapp_template_lang: str = "en"
    alert_whatsapp_api_version: str = "v26.0"
    # Hour (UTC) the daily digest and token check run. 05:00 UTC = 08:00 Riyadh.
    alert_digest_hour_utc: int = 5

    @property
    def whatsapp_recipients(self) -> list[str]:
        return [n.strip().lstrip("+") for n in self.alert_whatsapp_to.split(",") if n.strip()]

    @property
    def whatsapp_configured(self) -> bool:
        return bool(
            self.alert_whatsapp_token
            and self.alert_whatsapp_phone_number_id
            and self.whatsapp_recipients
        )


alert_settings = AlertSettings()


@dataclass
class Alert:
    kind: str
    title: str
    lines: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)  # webhook body (machine-readable)

    def text(self) -> str:
        return "\n".join([f"majra · {self.title}", *self.lines])


# ------------------------------------------------------------------ channels
def configured_channels() -> list[str]:
    out = []
    if alert_settings.whatsapp_configured:
        out.append("whatsapp")
    if core_settings.alert_webhook_url:
        out.append("webhook")
    return out


def alerts_configured() -> bool:
    return bool(configured_channels())


def _whatsapp_body(alert: Alert, to: str) -> dict:
    if alert_settings.alert_whatsapp_template:
        # Template variables may not contain newlines, tabs or 4+ spaces.
        flat = " · ".join([alert.title, *alert.lines])
        flat = " ".join(flat.split())[:WHATSAPP_MAX_CHARS]
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": alert_settings.alert_whatsapp_template,
                "language": {"code": alert_settings.alert_whatsapp_template_lang},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": flat}]}
                ],
            },
        }
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": alert.text()[:4000], "preview_url": False},
    }


def _send_whatsapp(alert: Alert) -> bool:
    url = (
        f"https://graph.facebook.com/{alert_settings.alert_whatsapp_api_version}/"
        f"{alert_settings.alert_whatsapp_phone_number_id}/messages"
    )
    headers = {"Authorization": f"Bearer {alert_settings.alert_whatsapp_token}"}
    ok = True
    for to in alert_settings.whatsapp_recipients:
        try:
            r = httpx.post(url, json=_whatsapp_body(alert, to), headers=headers, timeout=10)
            if r.status_code >= 400:
                ok = False
                log.warning("alert.whatsapp_failed", kind=alert.kind, status=r.status_code)
        except httpx.HTTPError as exc:
            ok = False
            log.warning("alert.whatsapp_failed", kind=alert.kind, error=type(exc).__name__)
    return ok


def _send_webhook(alert: Alert) -> bool:
    try:
        r = httpx.post(
            core_settings.alert_webhook_url,
            json={"event": alert.kind, **alert.payload},
            timeout=10,
        )
        return r.status_code < 400
    except httpx.HTTPError:
        log.warning("alert.webhook_failed", kind=alert.kind)
        return False


def send(alert: Alert) -> bool:
    """Deliver to every configured channel. True if at least one accepted it.
    Always logs the alert itself, so it is visible even with no channel set."""
    log.warning("alert", kind=alert.kind, title=alert.title)
    sent = False
    if alert_settings.whatsapp_configured:
        sent = _send_whatsapp(alert) or sent
    if core_settings.alert_webhook_url:
        sent = _send_webhook(alert) or sent
    return sent


# ------------------------------------------------------------- alert builders
def _conn_label(db: Session, connection_id: int | None) -> str:
    from app.models import Connection

    conn = db.get(Connection, connection_id) if connection_id else None
    return f"{conn.platform} · {conn.name}" if conn else "unknown connection"


def sync_failed(db: Session, sync_job_id: int | None, error: str) -> Alert:
    from app.models import SyncJob

    job = db.get(SyncJob, sync_job_id) if sync_job_id else None
    label = _conn_label(db, job.connection_id if job else None)
    lines = [label]
    if job:
        lines.append(f"range {job.date_from} → {job.date_to}")
    lines.append(f"error: {error[:300]}")
    return Alert(
        kind="sync.failed",
        title="sync failed",
        lines=lines,
        payload={"sync_job_id": sync_job_id, "error": error},
    )


def accounts_skipped(db: Session, job) -> Alert | None:
    stats = job.stats or {}
    n = stats.get("accounts_skipped") or 0
    if not n:
        return None
    ids = stats.get("accounts_skipped_ids") or []
    lines = [_conn_label(db, job.connection_id),
             f"{n} account(s) skipped, {stats.get('accounts', 0)} synced"]
    if ids:
        lines.append("skipped: " + ", ".join(ids[:10]))
    lines.append("their data for this range was NOT refreshed")
    return Alert(
        kind="accounts.skipped",
        title="accounts skipped during sync",
        lines=lines,
        payload={"sync_job_id": job.id, "connection_id": job.connection_id,
                 "accounts_skipped": n, "account_ids": ids},
    )


def expiring_tokens(db: Session, now: datetime | None = None,
                    warn_days: int = TOKEN_WARN_DAYS) -> list[dict]:
    """Active connections whose stored token expires within `warn_days` (or has
    already expired). Tokens without an expiry (refreshable / service accounts)
    are not reported."""
    from app.models import Connection

    now = now or datetime.now(UTC)
    out: list[dict] = []
    conns = db.scalars(
        select(Connection).where(
            Connection.deleted_at.is_(None), Connection.status != "disabled"
        ).order_by(Connection.id)
    )
    for c in conns:
        try:
            exp_raw = (decrypt_tokens(c.encrypted_tokens) or {}).get("expires_at")
        except Exception:  # noqa: BLE001 — unreadable token is its own problem
            continue
        if not exp_raw:
            continue
        try:
            exp = datetime.fromisoformat(str(exp_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        days_left = (exp - now).total_seconds() / 86400
        if days_left <= warn_days:
            out.append({
                "connection_id": c.id, "platform": c.platform, "name": c.name,
                "expires_at": exp.isoformat(), "days_left": int(days_left // 1),
            })
    return out


def token_expiring(items: list[dict]) -> Alert | None:
    if not items:
        return None
    lines = []
    for t in items:
        when = "EXPIRED" if t["days_left"] < 0 else f"in {t['days_left']} day(s)"
        lines.append(f"{t['platform']} · {t['name']}: token expires {when} "
                     f"({t['expires_at'][:10]})")
    lines.append("reconnect it from Connections before it lapses")
    return Alert(kind="token.expiring", title="platform token expiring",
                 lines=lines, payload={"connections": items})


def api_version_notices(today: date | None = None,
                        warn_days: int = API_VERSION_WARN_DAYS) -> list[dict]:
    """Pinned API versions whose published sunset is within `warn_days`."""
    from app.connectors.meta.connector import GRAPH_VERSION

    today = today or date.today()
    pinned = {"meta": GRAPH_VERSION}
    out = []
    for platform, version in pinned.items():
        sunset = API_SUNSETS.get(platform, {}).get(version)
        if sunset and (sunset - today).days <= warn_days:
            out.append({"platform": platform, "version": version,
                        "sunset": sunset.isoformat(),
                        "days_left": (sunset - today).days})
    return out


def api_version_alert(items: list[dict]) -> Alert | None:
    if not items:
        return None
    lines = []
    for n in items:
        if n.get("warning"):
            lines.append(f"{n['platform']} {n['version']}: {n['warning'][:300]}")
        else:
            lines.append(f"{n['platform']} API {n['version']} retires {n['sunset']} "
                         f"({n['days_left']} day(s)) — upgrade the connector")
    return Alert(kind="api.version", title="API version warning", lines=lines,
                 payload={"notices": items})


def daily_digest(db: Session, now: datetime | None = None) -> Alert:
    """Morning summary of the last 24h. Spend is shown per platform *in each
    account's own currency* — never blended (blending is a known T5 issue)."""
    from app.models import Account, Connection, FactAdPerformance, SyncJob
    from app.services.anomalies import anomaly_report
    from app.services.freshness import stale_connections
    from app.services.pacing import over_budget

    now = now or datetime.now(UTC)
    since = now - timedelta(hours=24)
    yesterday = (now - timedelta(days=1)).date()

    rows = db.execute(
        select(Connection.platform, SyncJob.status, func.count())
        .join(Connection, Connection.id == SyncJob.connection_id)
        .where(SyncJob.created_at >= since)
        .group_by(Connection.platform, SyncJob.status)
        .order_by(Connection.platform)
    ).all()
    per_platform: dict[str, dict[str, int]] = {}
    for platform, status, n in rows:
        per_platform.setdefault(platform, {})[status] = n
    skipped = db.scalar(
        select(func.count()).select_from(SyncJob).where(
            SyncJob.created_at >= since,
            SyncJob.stats["accounts_skipped"].as_integer() > 0,
        )
    ) or 0

    spend = db.execute(
        select(FactAdPerformance.platform, FactAdPerformance.currency,
               func.sum(FactAdPerformance.spend))
        .join(Account, Account.id == FactAdPerformance.account_id)
        .join(Connection, Connection.id == Account.connection_id)
        .where(FactAdPerformance.date == yesterday,
               FactAdPerformance.breakdown == "none",
               Connection.deleted_at.is_(None))
        .group_by(FactAdPerformance.platform, FactAdPerformance.currency)
        .order_by(FactAdPerformance.platform)
    ).all()

    stale = stale_connections(db, today=now.date())
    over = over_budget(db, today=now.date())
    anomalies = anomaly_report(db, today=now.date())
    tokens = expiring_tokens(db, now=now)
    versions = api_version_notices(today=now.date())
    reauth = [
        {"connection_id": c.id, "platform": c.platform, "name": c.name}
        for c in db.scalars(
            select(Connection).where(
                Connection.status == "needs_reauth", Connection.deleted_at.is_(None)
            ).order_by(Connection.id)
        )
    ]

    lines = [f"last 24h to {now:%Y-%m-%d %H:%M} UTC"]
    if per_platform:
        for p, st in per_platform.items():
            lines.append(f"syncs {p}: " + ", ".join(f"{k} {v}" for k, v in sorted(st.items())))
    else:
        lines.append("syncs: none ran")
    if skipped:
        lines.append(f"syncs with skipped accounts: {skipped}")
    if spend:
        lines.append(f"spend {yesterday}: " + "; ".join(
            f"{p} {float(s or 0):,.2f} {c or '?'}" for p, c, s in spend))
    else:
        lines.append(f"spend {yesterday}: no rows")
    needs = []
    if reauth:
        needs.append(f"{len(reauth)} connection(s) need reconnecting ("
                     + ", ".join(f"{r['platform']} · {r['name']}" for r in reauth) + ")")
    if stale:
        needs.append(f"{len(stale)} stale connection(s)")
    if over:
        needs.append(f"{len(over)} budget(s) projected over")
    if anomalies:
        needs.append(f"{len(anomalies)} spend anomal(y/ies)")
    if tokens:
        needs.append(f"{len(tokens)} token(s) expiring")
    if versions:
        needs.append(f"{len(versions)} API version notice(s)")
    lines.append("attention: " + (", ".join(needs) if needs else "nothing"))

    return Alert(
        kind="digest.daily",
        title="daily digest",
        lines=lines,
        payload={
            "syncs": per_platform, "syncs_with_skips": skipped,
            "spend": [{"platform": p, "currency": c, "spend": float(s or 0)}
                      for p, c, s in spend],
            "stale": stale, "over_budget": over, "anomalies": anomalies,
            "expiring_tokens": tokens, "api_versions": versions,
            "needs_reauth": reauth,
        },
    )
