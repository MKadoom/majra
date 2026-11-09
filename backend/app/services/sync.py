"""Sync engine (SPEC §4).

Write path is delete+insert scoped to (connection's accounts, breakdown, date
range) in one transaction — re-running any sync is idempotent. Raw pages are
appended to the raw layer first and are the re-normalization source of truth.
"""
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.connectors.base import (
    AuthError,
    PermanentError,
    TokenSet,
    TransientError,
    get_connector,
)
from app.core.observability import SYNC_ROWS, SYNC_SECONDS, SYNCS
from app.core.security import decrypt_tokens, encrypt_tokens
from app.models import (
    Account,
    AccountSyncState,
    Ad,
    Adset,
    Campaign,
    Connection,
    FactAdPerformance,
    FactSocialPerformance,
    FactWebPerformance,
    GtmResource,
    SyncJob,
    SyncLog,
)

log = structlog.get_logger()


def _log(db: Session, job: SyncJob, event: str, level: str = "info", **data) -> None:
    db.add(SyncLog(sync_job_id=job.id, level=level, event=event, data=data))
    db.flush()
    log.info(event, sync_job_id=job.id, **data)


# ------------------------------------------------------------- write safety
# Advisory-lock namespace for "one sync writes to a connection at a time".
SYNC_LOCK_CLASS = 7_201
# How long a sync waits for another sync on the same connection before giving up
# with a retryable error (the worker then retries with backoff).
SYNC_LOCK_TIMEOUT = "10min"


def _lock_connection(db: Session, connection_id: int) -> None:
    """Serialize writers per connection for the rest of this transaction.

    Two overlapping syncs of the same connection would each delete the range
    (seeing none of the other's uncommitted rows) and each insert, doubling the
    facts. A transaction-scoped advisory lock makes the second one wait until the
    first commits; it then deletes the first one's rows and the totals stay right.
    Released automatically at commit/rollback — so nothing may commit between
    here and the end of the sync."""
    from sqlalchemy.exc import OperationalError

    db.execute(text(f"SET LOCAL lock_timeout = '{SYNC_LOCK_TIMEOUT}'"))
    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:cls, :key)"),
            {"cls": SYNC_LOCK_CLASS, "key": connection_id},
        )
    except OperationalError as exc:
        raise TransientError(
            f"connection {connection_id} is busy with another sync"
        ) from exc


def _has_rows(db: Session, model, account_id: int, job, *extra) -> bool:
    return db.scalar(
        select(model.id).where(
            model.account_id == account_id,
            model.date >= job.date_from,
            model.date <= job.date_to,
            *extra,
        ).limit(1)
    ) is not None


def advance_coverage(db: Session, account_id: int, breakdown: str,
                     date_from: date, date_to: date) -> None:
    """Extend an account's synced range with a successful job's range — only
    when the two touch or overlap, so the range never papers over a gap."""
    st = db.get(AccountSyncState, (account_id, breakdown))
    if st is None:
        db.add(AccountSyncState(account_id=account_id, breakdown=breakdown,
                                synced_from=date_from, synced_to=date_to))
        db.flush()  # so a second call in this transaction finds it
        return
    one = timedelta(days=1)
    if date_from <= st.synced_to + one and date_to >= st.synced_from - one:
        st.synced_from = min(st.synced_from, date_from)
        st.synced_to = max(st.synced_to, date_to)


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
    adset_campaign: dict[int, int] = {}          # adset_id -> campaign_id
    ad_parents: dict[str, tuple[int | None, int | None]] = {}  # ad_ext -> (adset_id, campaign_id)

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
        adset_campaign[row.id] = parent

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
        ad_parents[e["external_id"]] = (parent, adset_campaign.get(parent))

    db.flush()
    return camp_map, adset_map, ad_map, ad_parents


def _resolve_dims(db, acc, norm, camp_map, adset_map, ad_map, ad_parents):
    """Map an insight row to internal dim ids. Insights may reference archived
    entities (create dims lazily from names), or — for ad-level-only reporters
    like TikTok/Snapchat — carry just the ad id, in which case the ad's
    campaign/adset are derived from the entity hierarchy (ad_parents)."""
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
    # derive missing parents from the ad's known hierarchy
    if a_ext and (campaign_id is None or adset_id is None):
        p_adset, p_campaign = ad_parents.get(a_ext, (None, None))
        adset_id = adset_id or p_adset
        campaign_id = campaign_id or p_campaign
    return campaign_id, adset_id, ad_id


# ------------------------------------------------------------------ main
def _append_raw(db, raw_model, conn, job, account_external_id, payload) -> None:
    if raw_model is None:
        return
    db.add(
        raw_model(
            connection_id=conn.id,
            account_external_id=account_external_id,
            params={
                "breakdown": job.breakdown,
                "date_from": job.date_from.isoformat(),
                "date_to": job.date_to.isoformat(),
            },
            payload=payload,
        )
    )


def _handle_ad_performance(db, conn, connector, job, accounts, tokens) -> dict:
    """Ad platforms (Meta, Google, TikTok, Snapchat): dims + fact_ad_performance."""
    raw_model = connector.raw_model
    total_rows = 0
    facts: list[dict] = []
    synced_acc_ids: list[int] = []   # accounts whose facts get delete+insert
    ok_acc_ids: list[int] = []       # accounts the platform answered for
    skipped: list[str] = []
    empty_kept: list[str] = []
    for acc in accounts:
        # Per-account isolation: one ad account returning a persistent error
        # (e.g. Meta HTTP 500 on a disabled/broken account) must not sink the
        # whole sync. Skip that account and keep the healthy ones. Its rows are
        # collected separately so a failure mid-pagination doesn't insert half
        # an account on top of data that was never deleted.
        acc_facts: list[dict] = []
        out_of_range = 0
        try:
            if getattr(connector, "insights_carry_hierarchy", False):
                # Insights self-describe the hierarchy (Meta): skip the entity
                # walk and let _resolve_dims build dims lazily from insight rows.
                # Fewer API calls → less rate-limit pressure.
                camp_map, adset_map, ad_map, ad_parents = {}, {}, {}, {}
            else:
                camp_map, adset_map, ad_map, ad_parents = _sync_entities(
                    db, connector, tokens, acc
                )
            fetched = 0
            for raw in connector.fetch_insights(
                tokens, acc.external_id, job.date_from, job.date_to, job.breakdown
            ):
                _append_raw(db, raw_model, conn, job, acc.external_id, raw)
                norm = connector.normalize_insight(raw, job.breakdown)
                day = date.fromisoformat(norm["date"])
                if not job.date_from <= day <= job.date_to:
                    # The delete half of delete+insert only covers the job's
                    # range, so a row dated outside it would never be replaced
                    # and would pile up on every re-sync. Raw keeps it.
                    out_of_range += 1
                    continue
                campaign_id, adset_id, ad_id = _resolve_dims(
                    db, acc, norm, camp_map, adset_map, ad_map, ad_parents
                )
                acc_facts.append(
                    dict(
                        date=day,
                        platform=acc.platform_override or conn.platform,
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
                        messages_sent=norm.get("messages_sent", 0),
                        messages_delivered=norm.get("messages_delivered", 0),
                        cost_source=norm.get("cost_source", "platform"),
                    )
                )
                fetched += 1
        except AuthError:
            # A dead token is not one bad account — every account will fail the
            # same way. Fail the job so the connection is flagged needs_reauth.
            raise
        except (TransientError, PermanentError, Exception) as exc:  # noqa: BLE001
            skipped.append(acc.external_id)
            _log(
                db, job, "account.skipped", level="warning",
                account=acc.external_id, error=f"{type(exc).__name__}: {exc}",
            )
            continue
        if not acc_facts and _has_rows(db, FactAdPerformance, acc.id, job,
                                       FactAdPerformance.breakdown == job.breakdown):
            # The platform answered with nothing for a range we already hold
            # data for. Far more likely a platform glitch than a real wipe, so
            # keep what we have instead of deleting it.
            empty_kept.append(acc.external_id)
            _log(db, job, "insights.empty_kept", level="warning",
                 account=acc.external_id)
            ok_acc_ids.append(acc.id)
            continue
        facts.extend(acc_facts)
        synced_acc_ids.append(acc.id)
        ok_acc_ids.append(acc.id)
        if out_of_range:
            _log(
                db, job, "insights.out_of_range", level="warning",
                account=acc.external_id, rows=out_of_range,
            )
        _log(db, job, "insights.fetched", account=acc.external_id, rows=fetched)
        total_rows += fetched

    if skipped and not ok_acc_ids:
        # Every account failed — surface it as a real failure.
        raise TransientError(
            f"{conn.platform}: all {len(skipped)} account(s) errored "
            f"(e.g. {skipped[0]})"
        )

    # Only delete+reinsert facts for accounts we actually synced this run, so a
    # skipped account keeps whatever data it already had (idempotent per account).
    acc_ids = synced_acc_ids
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
    return {
        "accounts": len(ok_acc_ids),
        "accounts_skipped": len(skipped),
        "accounts_skipped_ids": skipped,
        "accounts_empty_kept": empty_kept,
        "synced_account_ids": ok_acc_ids,
        "rows_fetched": total_rows,
        "rows_deleted": deleted,
        "rows_inserted": len(facts),
    }


def _handle_web_performance(db, conn, connector, job, accounts, tokens) -> dict:
    """Web analytics (GA4): no ad hierarchy; writes fact_web_performance,
    dimensioned by source/medium/campaign. Delete+insert scoped by
    (accounts, date range) — no breakdown column."""
    raw_model = connector.raw_model
    total_rows = 0
    facts: list[dict] = []
    write_ids: list[int] = []
    empty_kept: list[str] = []
    for acc in accounts:
        fetched = 0
        for raw in connector.fetch_insights(
            tokens, acc.external_id, job.date_from, job.date_to, job.breakdown
        ):
            _append_raw(db, raw_model, conn, job, acc.external_id, raw)
            norm = connector.normalize_insight(raw, job.breakdown)
            facts.append(
                dict(
                    date=date.fromisoformat(norm["date"]),
                    account_id=acc.id,
                    source=norm.get("source"),
                    medium=norm.get("medium"),
                    campaign_name=norm.get("campaign_name"),
                    sessions=norm.get("sessions", 0),
                    users=norm.get("users", 0),
                    conversions=norm.get("conversions", 0),
                    revenue=norm.get("revenue", 0),
                    purchases=norm.get("purchases", 0),
                )
            )
            fetched += 1
        _log(db, job, "report.fetched", account=acc.external_id, rows=fetched)
        total_rows += fetched
        if fetched == 0 and _has_rows(db, FactWebPerformance, acc.id, job):
            empty_kept.append(acc.external_id)
            _log(db, job, "insights.empty_kept", level="warning",
                 account=acc.external_id)
        else:
            write_ids.append(acc.id)

    acc_ids = write_ids
    deleted = 0
    if acc_ids:
        res = db.execute(
            delete(FactWebPerformance).where(
                FactWebPerformance.account_id.in_(acc_ids),
                FactWebPerformance.date >= job.date_from,
                FactWebPerformance.date <= job.date_to,
            )
        )
        deleted = res.rowcount or 0
    if facts:
        db.bulk_insert_mappings(FactWebPerformance, facts)
    return {
        "accounts": len(accounts),
        "accounts_empty_kept": empty_kept,
        "synced_account_ids": [a.id for a in accounts],
        "rows_fetched": total_rows,
        "rows_deleted": deleted,
        "rows_inserted": len(facts),
    }


# A connector declares its `dataset`; the engine picks the matching handler.
# Adding a connector that reuses an existing shape needs no engine change; a
# genuinely new data shape (like GA4's web analytics) adds one handler here.
DATASET_HANDLERS = {
    "ad_performance": _handle_ad_performance,
    "web_performance": _handle_web_performance,
    "social_performance": None,  # set below (defined after this comment)
}


def _handle_social_performance(db, conn, connector, job, accounts, tokens) -> dict:
    """Organic social (Facebook Pages / Instagram): writes
    fact_social_performance, rows keyed by (grain, external_ref). Delete+insert
    scoped by (accounts, date range); no breakdown."""
    raw_model = connector.raw_model
    total_rows = 0
    facts: list[dict] = []
    write_ids: list[int] = []
    empty_kept: list[str] = []
    for acc in accounts:
        fetched = 0
        for raw in connector.fetch_insights(
            tokens, acc.external_id, job.date_from, job.date_to, job.breakdown
        ):
            _append_raw(db, raw_model, conn, job, acc.external_id, raw)
            norm = connector.normalize_insight(raw, job.breakdown)
            facts.append(
                dict(
                    date=date.fromisoformat(norm["date"]),
                    account_id=acc.id,
                    grain=norm.get("grain", "page"),
                    external_ref=norm.get("external_ref"),
                    impressions=norm.get("impressions", 0),
                    reach=norm.get("reach", 0),
                    engagement=norm.get("engagement", 0),
                    followers=norm.get("followers", 0),
                )
            )
            fetched += 1
        _log(db, job, "social.fetched", account=acc.external_id, rows=fetched)
        total_rows += fetched
        if fetched == 0 and _has_rows(db, FactSocialPerformance, acc.id, job):
            empty_kept.append(acc.external_id)
            _log(db, job, "insights.empty_kept", level="warning",
                 account=acc.external_id)
        else:
            write_ids.append(acc.id)

    acc_ids = write_ids
    deleted = 0
    if acc_ids:
        res = db.execute(
            delete(FactSocialPerformance).where(
                FactSocialPerformance.account_id.in_(acc_ids),
                FactSocialPerformance.date >= job.date_from,
                FactSocialPerformance.date <= job.date_to,
            )
        )
        deleted = res.rowcount or 0
    if facts:
        db.bulk_insert_mappings(FactSocialPerformance, facts)
    return {
        "accounts": len(accounts),
        "accounts_empty_kept": empty_kept,
        "synced_account_ids": [a.id for a in accounts],
        "rows_fetched": total_rows,
        "rows_deleted": deleted,
        "rows_inserted": len(facts),
    }


DATASET_HANDLERS["social_performance"] = _handle_social_performance


def _handle_config_snapshot(db, conn, connector, job, accounts, tokens) -> dict:
    """Config snapshots (GTM): no date range, no facts. Each run APPENDS a full
    snapshot stamped with one captured_at (audit history) — not delete+insert,
    so re-running deliberately creates a new snapshot."""
    raw_model = connector.raw_model
    captured = datetime.now(UTC)
    total = 0
    rows: list[dict] = []
    for acc in accounts:
        n = 0
        for rec in connector.snapshot(tokens, acc.external_id):
            _append_raw(db, raw_model, conn, job, acc.external_id, rec)
            rows.append(
                dict(
                    account_id=acc.id,
                    kind=rec["kind"],
                    container_external_id=rec.get("container_external_id"),
                    workspace_external_id=rec.get("workspace_external_id"),
                    external_id=rec.get("external_id"),
                    name=rec.get("name"),
                    type=rec.get("type"),
                    data=rec.get("data") or {},
                    captured_at=captured,
                )
            )
            n += 1
        _log(db, job, "snapshot.captured", account=acc.external_id, resources=n)
        total += n
    if rows:
        db.bulk_insert_mappings(GtmResource, rows)
    return {
        "accounts": len(accounts),
        "rows_fetched": total,
        "rows_deleted": 0,          # append-only
        "rows_inserted": len(rows),
    }


DATASET_HANDLERS["config_snapshot"] = _handle_config_snapshot


def refresh_tokens_if_due(db: Session, conn, connector, tokens: TokenSet, job=None,
                          now: datetime | None = None) -> TokenSet:
    """Refresh the stored token before it expires and save it (encrypted) on
    the connection, so a rotated refresh token is never lost. A refresh the
    platform rejects raises AuthError, which fails the job and flags the
    connection needs_reauth. Token values are never logged."""
    if not connector.needs_refresh(tokens, now):
        return tokens
    fresh = connector.refresh(tokens)
    merged = TokenSet(
        access_token=fresh.access_token,
        refresh_token=fresh.refresh_token or tokens.refresh_token,
        expires_at=fresh.expires_at,
        extra=fresh.extra or tokens.extra,
    )
    if merged.to_dict() != tokens.to_dict():
        conn.encrypted_tokens = encrypt_tokens(merged.to_dict())
        db.commit()
        if job is not None:
            _log(db, job, "token.refreshed", expires_at=merged.expires_at)
    return merged


def run_sync(db: Session, sync_job_id: int) -> SyncJob:
    job = db.get(SyncJob, sync_job_id)
    if job is None:
        raise PermanentError(f"sync job {sync_job_id} not found")
    conn = db.get(Connection, job.connection_id)

    job.status = "running"
    job.started_at = datetime.now(UTC)
    db.commit()

    try:
        connector = get_connector(conn.platform)
        tokens = TokenSet.from_dict(decrypt_tokens(conn.encrypted_tokens))
        tokens = refresh_tokens_if_due(db, conn, connector, tokens, job)
        # From here to the final commit is ONE transaction holding the lock.
        _lock_connection(db, conn.id)

        accounts = _upsert_accounts(db, conn, connector.fetch_accounts(tokens))
        # Optional per-connection allow-list: sync only the chosen accounts. This
        # conserves the platform's per-user API rate budget (e.g. Meta dev-mode
        # limits) by not spending calls on read-only / empty accounts. NULL or
        # empty means "all", so existing connections are unaffected.
        wanted = set(conn.synced_account_ids or [])
        if wanted:
            accounts = [a for a in accounts if a.external_id in wanted]
        _log(db, job, "accounts.synced", count=len(accounts))

        handler = DATASET_HANDLERS.get(connector.dataset)
        if handler is None:
            raise PermanentError(f"no sync handler for dataset '{connector.dataset}'")
        stats = handler(db, conn, connector, job, accounts, tokens)
        # Deprecation warnings the platform sent back in response headers
        # (connectors that track them expose `api_warnings`). Kept on the job so
        # the worker can alert on them.
        warnings = sorted(getattr(connector, "api_warnings", None) or [])
        if warnings:
            stats = {**stats, "api_warnings": warnings}
            _log(db, job, "api.version_warning", level="warning", warnings=warnings)

        if connector.dataset != "config_snapshot":
            for acc_id in stats.get("synced_account_ids") or []:
                advance_coverage(db, acc_id, job.breakdown, job.date_from, job.date_to)
        job.status = "success"
        job.stats = stats
        job.finished_at = datetime.now(UTC)
        if conn.status == "needs_reauth":
            conn.status = "active"
        _log(db, job, "sync.finished", **stats)
        db.commit()

        SYNCS.labels(conn.platform, "success").inc()
        SYNC_ROWS.labels(conn.platform).inc(stats.get("rows_inserted", 0))
        if job.started_at:
            SYNC_SECONDS.labels(conn.platform).observe(
                (job.finished_at - job.started_at).total_seconds()
            )
        return job
    except Exception as exc:
        db.rollback()
        job = db.get(SyncJob, sync_job_id)
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.finished_at = datetime.now(UTC)
        if isinstance(exc, AuthError) and conn is not None:
            # surfaces the dashboard's "needs reauth" badge instead of failing silently
            db.get(Connection, conn.id).status = "needs_reauth"
        db.commit()
        SYNCS.labels(conn.platform if conn else "unknown", "failed").inc()
        raise


# ------------------------------------------------------------- watermarks
_FACT_BY_DATASET = {
    "ad_performance": FactAdPerformance,
    "web_performance": FactWebPerformance,
    "social_performance": FactSocialPerformance,
}


def watermark(db: Session, connection_id: int, breakdown: str) -> date | None:
    """Max synced date for a connection. Picks the fact table by the connector's
    dataset so each shape (ad / web / social) gets its own watermark."""
    conn = db.get(Connection, connection_id)
    dataset = get_connector(conn.platform).dataset if conn else "ad_performance"
    fact = _FACT_BY_DATASET.get(dataset, FactAdPerformance)
    stmt = (
        select(func.max(fact.date))
        .join(Account, Account.id == fact.account_id)
        .where(Account.connection_id == connection_id)
    )
    if dataset == "ad_performance":
        stmt = stmt.where(fact.breakdown == breakdown)
    return db.scalar(stmt)


def _tracked_accounts(db: Session, connection_id: int) -> list[int]:
    conn = db.get(Connection, connection_id)
    stmt = select(Account.id).where(Account.connection_id == connection_id)
    wanted = set(conn.synced_account_ids or []) if conn else set()
    if wanted:
        stmt = stmt.where(Account.external_id.in_(wanted))
    return list(db.scalars(stmt))


def account_watermark(db: Session, connection_id: int, breakdown: str) -> date | None:
    """Oldest `synced_to` among the connection's (allow-listed) accounts that
    have coverage — i.e. how far the laggiest known account has got."""
    ids = _tracked_accounts(db, connection_id)
    if not ids:
        return None
    return db.scalar(
        select(func.min(AccountSyncState.synced_to)).where(
            AccountSyncState.account_id.in_(ids),
            AccountSyncState.breakdown == breakdown,
        )
    )


def mark_backfills_complete(db: Session, connection_id: int, breakdown: str,
                            backfill_days: int, today: date | None = None) -> int:
    """Stamp backfill_completed_at on accounts whose coverage now reaches the
    backfill horizon. Returns how many were newly stamped."""
    yesterday = (today or date.today()) - timedelta(days=1)
    horizon = yesterday - timedelta(days=backfill_days)
    ids = _tracked_accounts(db, connection_id)
    if not ids:
        return 0
    rows = db.scalars(
        select(AccountSyncState).where(
            AccountSyncState.account_id.in_(ids),
            AccountSyncState.breakdown == breakdown,
            AccountSyncState.backfill_completed_at.is_(None),
            AccountSyncState.synced_from <= horizon,
        )
    ).all()
    for st in rows:
        st.backfill_completed_at = datetime.now(UTC)
    return len(rows)


def split_range(start: date, end: date, days: int = 30) -> list[tuple[date, date]]:
    """Split [start, end] into consecutive chunks of at most `days` days."""
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=days - 1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def incremental_range(
    db: Session,
    connection_id: int,
    breakdown: str,
    restatement_days: int,
    today: date | None = None,
) -> tuple[date, date] | None:
    """[watermark - restatement_days, yesterday]; None means backfill needed.

    The watermark is per ACCOUNT (app.account_sync_state): the connection
    resumes from the account that is furthest behind, so a healthy account can't
    hide another one's gap. Accounts that have never synced successfully don't
    hold the range back (they're still in every job). With no coverage recorded
    yet, falls back to the newest fact date (pre-0016 behaviour).

    `today` defaults to the wall clock but is injectable so the scheduler can
    pass its own reference time (keeps the whole computation deterministic).
    """
    wm = account_watermark(db, connection_id, breakdown)
    if wm is None:
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
