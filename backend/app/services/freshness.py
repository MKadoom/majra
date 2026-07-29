"""Data freshness monitors.

For each connection, compare its watermark (newest synced day) to yesterday. A
connection whose newest data is more than `grace` days behind — but that HAS
synced before — is 'stale': its source stopped delivering or its sync is
failing. Brand-new connections that never synced are reported (last_date null)
but not flagged stale, to avoid false alarms right after connecting.

Surfaced at GET /freshness and checked by a daily beat task that posts an alert
to MAJRA_ALERT_WEBHOOK_URL for connections that just went stale.
"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Connection
from app.services.sync import watermark

DEFAULT_GRACE_DAYS = 2


def freshness_report(
    db: Session, today: date | None = None, grace_days: int = DEFAULT_GRACE_DAYS
) -> list[dict]:
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    out: list[dict] = []
    conns = db.scalars(
        select(Connection).where(Connection.status != "disabled").order_by(Connection.id)
    )
    for c in conns:
        wm = watermark(db, c.id, "none")
        days_stale = (yesterday - wm).days if wm else None
        stale = wm is not None and days_stale > grace_days
        out.append(
            {
                "connection_id": c.id,
                "platform": c.platform,
                "name": c.name,
                "last_date": wm.isoformat() if wm else None,
                "days_stale": days_stale,
                "stale": stale,
            }
        )
    return out


def stale_connections(
    db: Session, today: date | None = None, grace_days: int = DEFAULT_GRACE_DAYS
) -> list[dict]:
    return [r for r in freshness_report(db, today, grace_days) if r["stale"]]
