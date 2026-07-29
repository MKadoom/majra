"""Auto-refresh exchange rates from a public provider.

The provider returns units-per-USD (e.g. SAR: 3.75, meaning 1 USD = 3.75 SAR).
We store `rate_to_usd` = USD value of one unit = 1 / units-per-USD, so SAR ends
up ≈ 0.2667. Upserts every currency the provider lists; USD stays implicitly 1.

Called on demand (POST /fx-rates/refresh) and by a weekly beat task. Manual
rates set via PUT /fx-rates are simply overwritten on the next refresh — set a
custom provider (or skip the refresh) if you want to pin your own.
"""
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import FxRate


def refresh_fx_rates(db: Session, url: str | None = None) -> int:
    url = url or settings.fx_provider_url
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # open.er-api.com uses "rates"; some providers use "conversion_rates".
    rates = data.get("rates") or data.get("conversion_rates") or {}
    if not rates:
        raise ValueError("fx provider returned no rates")

    updated = 0
    for ccy, per_usd in rates.items():
        try:
            per_usd = float(per_usd)
        except (TypeError, ValueError):
            continue
        if per_usd <= 0:
            continue
        rate_to_usd = 1.0 / per_usd
        row = db.scalar(select(FxRate).where(FxRate.currency == ccy))
        if row is None:
            db.add(FxRate(currency=ccy, rate_to_usd=rate_to_usd))
        else:
            row.rate_to_usd = rate_to_usd
        updated += 1
    db.commit()
    return updated
