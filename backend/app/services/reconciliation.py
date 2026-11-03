"""Daily reconciliation (T7).

For every healthy connection whose connector can report its own account-level
totals (`fetch_account_totals`), compare those totals — per account, per day,
for spend / impressions / purchases — with the sum of the ad-level rows majra
stored (breakdown 'none', native currency, no conversion). A disagreement means
the sync dropped something (a page, an ad, a day) or normalized it wrongly.

Window: by default the 7 days ending TWO days ago. The last day or two are
still being restated by the platforms and may not have been re-synced since
they closed, so comparing them would only produce noise.

Read-only against the platforms: one extra account-level call per account per
day. Results are stored in app.reconciliation_results; mismatches raise one
`data.mismatch` alert. Errors (e.g. a rate-limited account) are recorded but
not alerted — skipped accounts already alert on their own (T2).
"""
from datetime import date, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors.base import TokenSet, get_connector
from app.core.security import decrypt_tokens
from app.models import Account, Connection, FactAdPerformance, ReconciliationResult

log = structlog.get_logger()

METRICS = ("spend", "impressions", "purchases")
# Allowed difference: the larger of an absolute floor and a relative share.
TOLERANCE = {
    "spend": (1.0, 0.01),        # 1 unit of currency or 1%
    "impressions": (10, 0.01),
    "purchases": (1, 0.02),
}
DEFAULT_DAYS = 7
DEFAULT_LAG = 2


def within(metric: str, platform: float, stored: float) -> bool:
    floor, rel = TOLERANCE[metric]
    return abs(platform - stored) <= max(floor, rel * max(abs(platform), abs(stored)))


def _stored(db: Session, account_id: int, d1: date, d2: date) -> dict[str, dict]:
    rows = db.execute(
        select(FactAdPerformance.date,
               func.sum(FactAdPerformance.spend),
               func.sum(FactAdPerformance.impressions),
               func.sum(FactAdPerformance.purchases))
        .where(FactAdPerformance.account_id == account_id,
               FactAdPerformance.breakdown == "none",
               FactAdPerformance.date >= d1, FactAdPerformance.date <= d2)
        .group_by(FactAdPerformance.date)
    ).all()
    return {d.isoformat(): {"spend": float(s or 0), "impressions": int(i or 0),
                            "purchases": int(p or 0)} for d, s, i, p in rows}


def reconcile(db: Session, today: date | None = None, days: int = DEFAULT_DAYS,
              lag: int = DEFAULT_LAG, user_id: int | None = None) -> dict:
    today = today or date.today()
    d2 = today - timedelta(days=lag)
    d1 = d2 - timedelta(days=days - 1)
    stmt = select(Connection).where(
        Connection.deleted_at.is_(None), Connection.status == "active")
    if user_id is not None:
        stmt = stmt.where(Connection.user_id == user_id)

    summary = {"from": d1.isoformat(), "to": d2.isoformat(),
               "checked": 0, "mismatches": [], "errors": []}
    for conn in db.scalars(stmt.order_by(Connection.id)):
        connector = get_connector(conn.platform)
        if connector.dataset != "ad_performance":
            continue
        tokens = TokenSet.from_dict(decrypt_tokens(conn.encrypted_tokens))
        accounts = db.scalars(select(Account).where(Account.connection_id == conn.id))
        wanted = set(conn.synced_account_ids or [])
        for acc in accounts:
            if wanted and acc.external_id not in wanted:
                continue
            try:
                theirs = connector.fetch_account_totals(tokens, acc.external_id, d1, d2)
            except Exception as exc:  # noqa: BLE001 — record, don't crash the run
                theirs = None
                msg = f"{type(exc).__name__}: {exc}"[:500]
                db.add(ReconciliationResult(connection_id=conn.id, account_id=acc.id,
                                            date=d2, metric="*", status="error",
                                            detail=msg))
                summary["errors"].append({"account": acc.external_id, "error": msg})
                continue
            if theirs is None:
                break  # connector doesn't support reconciliation
            ours = _stored(db, acc.id, d1, d2)
            day = d1
            while day <= d2:
                k = day.isoformat()
                p_day = theirs.get(k, {})
                s_day = ours.get(k, {})
                for m in METRICS:
                    pv = float(p_day.get(m, 0) or 0)
                    sv = float(s_day.get(m, 0) or 0)
                    ok = within(m, pv, sv)
                    db.add(ReconciliationResult(
                        connection_id=conn.id, account_id=acc.id, date=day, metric=m,
                        platform_value=pv, stored_value=sv,
                        status="ok" if ok else "mismatch"))
                    summary["checked"] += 1
                    if not ok:
                        summary["mismatches"].append({
                            "platform": conn.platform, "account": acc.name,
                            "account_external_id": acc.external_id, "date": k,
                            "metric": m, "platform_value": round(pv, 2),
                            "stored_value": round(sv, 2),
                        })
                day += timedelta(days=1)
    db.commit()
    log.info("reconciliation.done", checked=summary["checked"],
             mismatches=len(summary["mismatches"]), errors=len(summary["errors"]))
    return summary


def mismatch_alert(summary: dict):
    from app.services.alerts import Alert

    mm = summary["mismatches"]
    if not mm:
        return None
    lines = [f"{summary['from']} → {summary['to']}: {len(mm)} mismatch(es) "
             f"of {summary['checked']} checks"]
    for m in mm[:8]:
        lines.append(f"{m['platform']} · {m['account']} {m['date']} {m['metric']}: "
                     f"platform {m['platform_value']:,} vs stored {m['stored_value']:,}")
    if len(mm) > 8:
        lines.append(f"… and {len(mm) - 8} more (GET /reconciliation)")
    lines.append("re-sync that range; if it persists, the connector is dropping data")
    return Alert(kind="data.mismatch", title="stored data disagrees with the platform",
                 lines=lines, payload=summary)
