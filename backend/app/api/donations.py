"""Jood order import (ground-truth donations) and claims-vs-truth comparison."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.api.keys import get_read_principal
from app.core.db import get_db
from app.models import DonationOrder, User
from app.services import donations as svc

router = APIRouter(prefix="/donations", tags=["donations (Jood)"])


class ImportIn(BaseModel):
    filename: str = Field(..., examples=["jood-orders-2026-11.csv"])
    # CSV text, or the raw file as base64 (required for .xlsx).
    content: str | None = None
    content_base64: str | None = None
    # canonical field -> column header in the file, for anything not auto-detected:
    # order_id, ordered_at, amount, currency, status, platform, utm_source,
    # utm_medium, utm_campaign, link
    mapping: dict[str, str] | None = None
    # Jood per-platform ad links: substring of the link -> platform
    # (meta | snapchat | tiktok | google_ads | whatsapp | unattributed)
    link_platforms: dict[str, str] | None = None
    default_currency: str = Field("SAR", min_length=3, max_length=8)
    # Preview by default: detect columns and count, write nothing.
    dry_run: bool = True


@router.post("/import", summary="Import a Jood order export (dry run by default)")
def import_orders(body: ImportIn, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Upload Jood's order export. Call with `dry_run: true` first to see the
    detected column mapping, statuses and per-platform counts; then again with
    `dry_run: false` to save. Re-importing the same export updates orders in
    place (keyed by order id), so overlapping exports are safe."""
    try:
        res = svc.import_orders(
            db, user.id, filename=body.filename, content=body.content,
            content_base64=body.content_base64, mapping=body.mapping,
            link_platforms=body.link_platforms,
            default_currency=body.default_currency.upper(), dry_run=body.dry_run)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return res.as_dict()


@router.get("", summary="Imported Jood donations per platform")
def summary(date_from: date, date_to: date,
            currency: str = Query("SAR", min_length=3, max_length=8),
            user: User = Depends(get_read_principal), db: Session = Depends(get_db)):
    try:
        return svc.donation_summary(db, user.id, date_from, date_to, currency)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/compare", summary="Each platform's claimed purchases vs Jood donations")
def compare(date_from: date, date_to: date,
            currency: str = Query("SAR", min_length=3, max_length=8),
            user: User = Depends(get_read_principal), db: Session = Depends(get_db)):
    """Per platform: purchases and value the platform claims (as synced into
    majra) next to the paid Jood orders attributed to it, with the ratio,
    cost per real donation and ROAS on Jood's amounts."""
    try:
        return svc.compare(db, user.id, date_from, date_to, currency)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/orders", summary="List imported Jood orders")
def orders(date_from: date, date_to: date, platform: str | None = None,
           limit: int = Query(200, ge=1, le=2000),
           user: User = Depends(get_read_principal), db: Session = Depends(get_db)):
    stmt = select(DonationOrder).where(
        DonationOrder.user_id == user.id, DonationOrder.date >= date_from,
        DonationOrder.date <= date_to)
    if platform:
        stmt = stmt.where(DonationOrder.platform == platform)
    rows = db.scalars(stmt.order_by(DonationOrder.ordered_at.desc()).limit(limit))
    return [{"order_id": o.external_id, "ordered_at": o.ordered_at.isoformat(),
             "date": o.date.isoformat(), "amount": float(o.amount), "currency": o.currency,
             "status": o.status, "is_paid": o.is_paid, "platform": o.platform,
             "utm_source": o.utm_source, "utm_campaign": o.utm_campaign}
            for o in rows]
