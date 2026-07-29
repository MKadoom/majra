from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.db import get_db
from app.models import SavedReport, User
from app.services.datasets import DATASETS
from app.services.reports import run_report

router = APIRouter(prefix="/reports", tags=["saved reports"])

DESTINATIONS = ("google_sheets", "webhook", "email", "slack", "teams")


class ReportIn(BaseModel):
    name: str
    dataset: str = "ads"
    # The query. `last_days` makes the window roll: a report that runs every
    # morning should mean "the last 30 days", not a range frozen at creation.
    params: dict = Field(default_factory=lambda: {"last_days": 30, "group_by": ["platform"]})
    destination: dict
    cadence: str = Field("daily", pattern="^(daily|weekly)$")
    hour_utc: int = Field(6, ge=0, le=23)
    enabled: bool = True


def _out(r: SavedReport) -> dict:
    dest = dict(r.destination or {})
    # Never hand secrets back out.
    dest.pop("service_account", None)
    dest.pop("secret", None)
    return {
        "id": r.id,
        "name": r.name,
        "dataset": r.dataset,
        "params": r.params,
        "destination": dest,
        "cadence": r.cadence,
        "hour_utc": r.hour_utc,
        "enabled": r.enabled,
        "last_run_at": r.last_run_at,
        "last_status": r.last_status,
        "last_error": r.last_error,
    }


def _own(db: Session, user: User, report_id: int) -> SavedReport:
    r = db.get(SavedReport, report_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "report not found")
    return r


@router.post("", status_code=201, summary="Save a report that runs on a schedule")
def create_report(
    body: ReportIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.dataset not in DATASETS:
        raise HTTPException(422, f"dataset must be one of {DATASETS}")
    kind = (body.destination or {}).get("kind")
    if kind not in DESTINATIONS:
        raise HTTPException(422, f"destination.kind must be one of {DESTINATIONS}")
    r = SavedReport(user_id=user.id, **body.model_dump())
    db.add(r)
    db.commit()
    return _out(r)


@router.get("", summary="List saved reports")
def list_reports(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(SavedReport)
        .where(SavedReport.user_id == user.id)
        .order_by(SavedReport.id.desc())
    )
    return [_out(r) for r in rows]


@router.patch("/{report_id}", summary="Pause or resume a report")
def toggle_report(
    report_id: int,
    enabled: bool,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _own(db, user, report_id)
    r.enabled = enabled
    db.commit()
    return _out(r)


@router.delete("/{report_id}", summary="Delete a report")
def delete_report(
    report_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _own(db, user, report_id)
    db.delete(r)
    db.commit()
    return {"id": report_id, "deleted": True}


@router.post("/{report_id}/run", summary="Run a report now")
def run_now(
    report_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _own(db, user, report_id)
    try:
        result = run_report(db, r)
    except Exception as exc:
        raise HTTPException(502, f"delivery failed: {exc}") from exc
    return {"id": r.id, **result}
