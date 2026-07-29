"""Spend anomaly detection.

For each account, compare the most recent complete day's spend to the mean of
the preceding days in a trailing window. A deviation beyond `threshold` (default
±50%) is flagged as a 'spike' (up) or 'drop' (down) — the kind of overnight
change a media buyer wants to hear about the next morning.

Surfaced at GET /anomalies and checked by a daily beat task that posts a
'spend.anomaly' alert to MAJRA_ALERT_WEBHOOK_URL.
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services.metrics import query_metrics

DEFAULT_LOOKBACK = 14
DEFAULT_THRESHOLD = 0.5
MIN_DAYS = 3  # need a few prior days for a stable baseline


def anomaly_report(
    db: Session,
    today: date | None = None,
    lookback: int = DEFAULT_LOOKBACK,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict]:
    today = today or date.today()
    end = today - timedelta(days=1)  # yesterday = last complete day
    start = end - timedelta(days=lookback)

    res = query_metrics(
        db,
        date_from=start,
        date_to=end,
        breakdown="none",
        group_by=["account", "date"],
        page_size=1_000_000,
    )

    by_acc: dict[str, dict[str, float]] = {}
    for row in res["data"]:
        acc = str(row.get("account") or "—")
        by_acc.setdefault(acc, {})[str(row["date"])] = float(row["spend"])

    out: list[dict] = []
    for acc, days in by_acc.items():
        dates = sorted(days)
        if len(dates) < MIN_DAYS + 1:
            continue
        latest = days[dates[-1]]
        prior = [days[d] for d in dates[:-1]]
        mean = sum(prior) / len(prior)
        if mean <= 0:
            continue
        dev = (latest - mean) / mean
        if abs(dev) >= threshold:
            out.append(
                {
                    "account": acc,
                    "date": dates[-1],
                    "latest_spend": round(latest, 2),
                    "avg_spend": round(mean, 2),
                    "deviation": round(dev, 4),
                    "direction": "spike" if dev > 0 else "drop",
                }
            )
    return out
