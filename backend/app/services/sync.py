"""Sync engine (SPEC §4).

Write path is delete+insert scoped to (connection's accounts, breakdown, date
range) in one transaction — re-running any sync is idempotent. Raw pages are
appended to the raw layer first and are the re-normalization source of truth.
"""
from datetime import date, datetime, timedelta, timezone

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.connectors.base import PermanentError, TokenSet, get_connector
from app.core.security import decrypt_tokens
from app.models import (
    Account, Ad, Adset, Campaign, Connection, FactAdPerformance,
    SyncJob, SyncLog,
)

log = structlog.get_logger()


def _log(db: Session, job: SyncJob, event: str, level: str = "info", **data) -> None:
    db.add(SyncLog(sync_job_id=job.id, level=level, event=event, data=data))
    db.flush()
    log.info(event, sync_job_id=job.id, **data)


# ------------------------------------------------------------------ dims
def _upsert_accounts(db: Session, conn: Connection, infos) -> list[Account]:
    out = []
    for info in infos:
        acc = db.scalar(
            select(Account).where(
                Account.platform == conn.platform,
                Account.external_id == info.external_id,
            )
        )
        if acc is None:
            acc = Account(
                connection_id=conn.id,
                platform=conn.platform,
                external_id=info.external_id,
                name=info.name,
            )
            db.add(acc)
        acc.name = info.name
        acc.currency = info.currency
        acc.timezone = info.timezone
        acc.connection_id = conn.id
        out.append(acc)
    db.flush()
    return out


def _upsert_dim(db, model, parent_col, parent_id, ext_id, name, **extra):
    row = db.scalar(
        select(model).where(
            getattr(model, parent_col) == parent_id, model.external_id == ext_id
        )
    )
    if row is None:
        row = model(**{parent_col: parent_id}, external_id=ext_id, name=name or ext_id)
        db.add(row)
        db.flush()
    if name:
        row.name = name
    for k, v in extra.items():
        if v is not None:
            setattr(row, k, v)
    return row


def _sync_entities(db: Session, connector, tokens: TokenSet, acc: Account):
    """Fetch campaign/adset/ad entities and return external-id → internal-id maps."""
    camp_map: dict[str, int] = {}
    adset_map: dict[str, int] = {}
    ad_map: dict[str, int] = {}

    for raw in connector.fetch_entities(tokens, acc.external_id, "campaign"):
        e = connector.normalize_entity(raw, "campaign")
        row = _upsert_dim(
            db, Campaign, "account_id", acc.id, e["external_id"], e["name"],
            status=e.get("status"), objective=e.get("objective"),
        )
        camp_map[e["external_id"]] = row.id

    for raw in connector.fetch_entities(tokens, acc.external_id, "adset"):
        e = connector.normalize_entity(raw, "adset")
        parent = camp_map.get(e.get("campaign_external_id"))
        if parent is None:
            parent = _upsert_dim(
                db, Campaign, "account_id", acc.id,
                e.get("campaign_external_id") or f"unknown-{e['external_id']}", None,
            ).id
            camp_map[e.get("campaign_external_id")] = parent
        row = _upsert_dim(
            db, Adset, "campaign_id", parent, e["external_id"], e["name"],
            status=e.get("status"),
        )
        adset_map[e["external_id"]] = row.id

    for raw in connector.fetch_entities(tokens, acc.external_id, "ad"):
        e = connector.normalize_entity(raw, "ad")
        parent = adset_map.get(e.get("adset_external_id"))
        if parent is None:
            continue  # ad without known adset: dim created lazily from insights
        row = _upsert_dim(
            db, Ad, "adset_id", parent, e["external_id"], e["name"],
            status=e.get("status"),
        )
        ad_map[e["external_id"]] = row.id

    db.flush()
    return camp_map, adset_map, ad_map


def _resolve_dims(db, acc, norm, camp_map, adset_map, ad_map):
    """Insights can reference archived entities missing from entity fetches —
    create minimal dims lazily from the insight row's names."""
    c_ext, s_ext, a_ext = (
        norm.get("campaign_external_id"),
        norm.get("adset_external_id"),
        norm.get("ad_external_id"),
    )
    campaign_id = camp_map.get(c_ext)
    if c_ext and campaign_id is None:
        campaign_id = _upsert_dim(
            db, Campaign, "account_id", acc.id, c_ext, norm.get("campaign_name")
        ).id
        camp_map[c_ext] = campaign_id
    adset_id = adset_map.get(s_ext)
    if s_ext and adset_id is None and campaign_id:
        adset_id = _upsert_dim(
            db, Adset, "campaign_id", campaign_id, s_ext, norm.get("adset_name")
        ).id
        adset_map[s_ext] = adset_id
    ad_id = ad_map.get(a_ext)
    if a_ext and ad_id is None and adset_id:
        ad_id = _upsert_dim(
            db, Ad, "adset_id", adset_id, a_ext, norm.get("ad_name")
        ).id
        ad_map[a_ext] = ad_id
    return campaign_id, adset_id, ad_id


# ------------------------------------------------------------------ main
def run_sync(db: Session, sync_job_id: int) -> SyncJob:
    job = db.get(SyncJob, sync_job_id)
    if job is None:
        raise PermanentError(f"sync job {sync_job_id} not found")
    conn = db.get(Connection, job.connection_id)

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.commit()

    try:
        connector = get_connector(conn.platform)
        tokens = TokenSet.from_dict(decrypt_tokens(conn.encrypted_tokens))
        raw_model = connector.raw_model

        accounts = _upsert_accounts(db, conn, connector.fetch_accounts(tokens))
        _log(db, job, "accounts.synced", count=len(accounts))

        total_rows = 0
        facts: list[dict] = []
        for acc in accounts:
            camp_map, adset_map, ad_map = _sync_entities(db, connector, tokens, acc)
            fetched = 0
            for raw in connector.fetch_insights(
                tokens, acc.external_id, job.date_from, job.date_to, job.breakdown
            ):
                if raw_model is not None:
                    db.add(
                        raw_model(
                            connection_id=conn.id,
                            account_external_id=acc.external_id,
                            params={
                                "breakdown": job.breakdown,
                                "date_from": job.date_from.isoformat(),
                                "date_to": job.date_to.isoformat(),
                            },
                            payload=raw,
                        )
                    )
                norm = connector.normalize_insight(raw, job.breakdown)
                campaign_id, adset_id, ad_id = _resolve_dims(
                    db, acc, norm, camp_map, adset_map, ad_map
                )
                facts.append(
                    dict(
                        date=date.fromisoformat(norm["date"]),
                        platform=conn.platform,
                        account_id=acc.id,
                        campaign_id=campaign_id,
                        adset_id=adset_id,
                        ad_id=ad_id,
                        breakdown=job.breakdown,
                        country=norm.get("country"),
                        device=norm.get("device"),
                        age=norm.get("age"),
                        gender=norm.get("gender"),
                        placement=norm.get("placement"),
                        spend=norm["spend"],
                        currency=acc.currency,
                        impressions=norm["impressions"],
                        clicks=norm["clicks"],
                        conversions=norm["conversions"],
                        conversion_value=norm["conversion_value"],
                        video_views=norm["video_views"],
                        leads=norm["leads"],
                        purchases=norm["purchases"],
                    )
                )
                fetched += 1
            _log(db, job, "insights.fetched", account=acc.external_id, rows=fetched)
            total_rows += fetched

        # Idempotent write: delete the exact scope, then insert (one transaction).
        acc_ids = [a.id for a in accounts]
        deleted = 0
        if acc_ids:
            res = db.execute(
                delete(FactAdPerformance).where(
                    FactAdPerformance.account_id.in_(acc_ids),
                    FactAdPerformance.breakdown == job.breakdown,
                    FactAdPerformance.date >= job.date_from,
                    FactAdPerformance.date <= job.date_to,
                )
            )
            deleted = res.rowcount or 0
        if facts:
            db.bulk_insert_mappings(FactAdPerformance, facts)

        job.status = "success"
        job.stats = {
            "accounts": len(accounts),
            "rows_fetched": total_rows,
            "rows_deleted": deleted,
            "rows_inserted": len(facts),
        }
        job.finished_at = datetime.now(timezone.utc)
        _log(db, job, "sync.finished", **job.stats)
        db.commit()
        return job
    except Exception as exc:
        db.rollback()
        job = db.get(SyncJob, sync_job_id)
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise


# ------------------------------------------------------------- watermarks
def watermark(db: Session, connection_id: int, breakdown: str) -> date | None:
    return db.scalar(
        select(func.max(FactAdPerformance.date))
        .join(Account, Account.id == FactAdPerformance.account_id)
        .where(
            Account.connection_id == connection_id,
            FactAdPerformance.breakdown == breakdown,
        )
    )


def incremental_range(
    db: Session,
    connection_id: int,
    breakdown: str,
    restatement_days: int,
    today: date | None = None,
) -> tuple[date, date] | None:
    """[watermark - restatement_days, yesterday]; None means backfill needed.

    `today` defaults to the wall clock but is injectable so the scheduler can
    pass its own reference time (keeps the whole computation deterministic).
    """
    wm = watermark(db, connection_id, breakdown)
    if wm is None:
        return None
    yesterday = (today or date.today()) - timedelta(days=1)
    start = min(wm - timedelta(days=restatement_days), yesterday)
    return start, yesterday


def backfill_chunks(backfill_days: int, today: date | None = None) -> list[tuple[date, date]]:
    """Month-sized chunks covering the last `backfill_days`, oldest first."""
    yesterday = (today or date.today()) - timedelta(days=1)
    start = yesterday - timedelta(days=backfill_days)
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= yesterday:
        end = min(cur + timedelta(days=29), yesterday)
        chunks.append((cur, end))
        cur = end + timedelta(days=1)
    return chunks
