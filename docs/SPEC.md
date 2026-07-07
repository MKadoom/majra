# majra SPEC — single source of truth

Every code session (and every Claude Code session) reads this file first.
Changes here are made deliberately by the owner, never as a side effect of a
feature. This document describes what is **built and tested** through Phase 3
plus the contracts the later phases must honor.

## §1 Purpose
Self-hosted pipeline: marketing-platform APIs → raw storage → normalized
warehouse → REST API (+ dashboard, Phase 6). Platforms planned: Meta Ads
(built), Google Ads, GA4, TikTok Ads, Snapchat Ads, Facebook Pages, Instagram
Insights, GTM (config only). Everything is one FastAPI backend, Postgres, Redis,
and Celery.

## §2 Connector contract (`app/connectors/base.py`)
Every connector subclasses `BaseConnector`, sets a `platform` string, and is
auto-registered via `__init_subclass__`. A connector touches nothing outside its
own package.

    class BaseConnector(ABC):
        platform: ClassVar[str]                      # "meta", "google_ads", ...
        # auth
        auth_url(state, redirect_uri) -> str
        exchange_code(code, redirect_uri) -> TokenSet
        refresh(tokens) -> TokenSet
        # discovery
        fetch_accounts(tokens) -> list[AccountInfo]
        # data — iterators that yield raw dicts, already paginated
        fetch_entities(tokens, account_external_id, level) -> Iterator[dict]
        fetch_insights(tokens, account_external_id, date_from, date_to,
                       breakdown) -> Iterator[dict]
        # normalization — PURE functions, unit-tested from fixtures
        normalize_entity(raw, level) -> dict
        normalize_insight(raw, breakdown) -> dict

`level` is one of `campaign | adset | ad`. Errors: raise `TransientError`
(retryable: rate limit, 5xx, timeout) or `PermanentError` (auth revoked, invalid
request). Nothing else escapes a connector. `get_connector(platform)` returns an
instance; unknown platform raises `PermanentError`.

Breakdown sets a connector may support (`BREAKDOWN_SETS`): `none`, `country`,
`device`, `age_gender`. `none` is mandatory.

## §3 Normalized schema (schema `norm`)
Dimensions (`app/models/norm_models.py`):

    accounts(id, connection_id, platform, external_id, name, currency, timezone,
             UNIQUE(platform, external_id))
    campaigns(id, account_id, external_id, name, status, objective,
              UNIQUE(account_id, external_id))
    adsets(id, campaign_id, external_id, name, status,
           UNIQUE(campaign_id, external_id))
    ads(id, adset_id, external_id, name, status, UNIQUE(adset_id, external_id))

Fact — daily paid media:

    fact_ad_performance(
      id, date, platform, account_id, campaign_id?, adset_id?, ad_id?,
      breakdown NOT NULL DEFAULT 'none',       -- none|country|device|age_gender
      country?, device?, age?, gender?, placement?,
      spend NUMERIC(18,6), currency, impressions BIGINT, clicks BIGINT,
      conversions NUMERIC(18,6), conversion_value NUMERIC(18,6),
      video_views BIGINT, leads BIGINT, purchases BIGINT)
    indexes: (date, account_id, breakdown), (campaign_id, date)

RULES:
- Rows from different breakdown sets MUST NOT be summed together. Every query
  filters on a single `breakdown` (default `none`). This prevents double
  counting: the country breakdown of one day sums to the same spend as the
  `none` row for that day.
- The fact table has **no unique constraint**. Idempotency is guaranteed by the
  write path (§4), not by upsert: each sync deletes its exact scope and
  re-inserts. This is simpler and handles rows that vanish from the source.
- Derived metrics (ctr, cpc, cpm, cpa, roas) are computed after aggregation in
  `app/services/metrics.py`, never stored.
- `spend` stays in the account's currency; `currency` travels with every fact
  row. Blended cross-currency totals are the caller's concern (v2: an fx table).
- `date` is the platform-reported day in the account's own timezone — that is
  how every platform reports dailies. Do not convert.
- Month partitioning of the fact table is a v2 migration; not needed at current
  volumes.

Other facts (later phases): `fact_web_performance` (GA4), `fact_social_performance`
(Pages/IG), `gtm_*` config-snapshot tables with a `captured_at` column.

Raw layer (schema `raw`): one append-only table per platform+endpoint —
`meta_insights(id, connection_id, account_external_id, params jsonb,
payload jsonb, fetched_at)`. Raw is the re-normalization source of truth and is
never mutated.

## §4 Sync semantics (`app/services/sync.py`, `app/services/schedules.py`)
- Watermark = `max(date)` present per (connection, breakdown set).
- A scheduled run fetches `[watermark - restatement_days, yesterday]`;
  `restatement_days` default 7 (platforms restate conversions retroactively).
- Write path (`run_sync`): append raw pages, upsert dims, then **delete+insert**
  facts scoped to (this connection's accounts, this breakdown, this date range)
  in one transaction → every sync is idempotent and safe to retry.
- First run after connect (no watermark): backfill `backfill_days` (default 365)
  in month-sized chunks, one `sync_job` per chunk, oldest first.
- Time is injectable: `incremental_range(..., today=)` and
  `backfill_chunks(..., today=)` accept a reference date so the scheduler is
  deterministic and testable; they default to the wall clock.
- Job kinds: `manual | scheduled | backfill`. Retries (Celery): autoretry up to
  5× with exponential backoff on `TransientError`; `PermanentError` fails the job
  immediately with `error = "ClassName: message"`. On final failure the worker
  POSTs a JSON alert to `MAJRA_ALERT_WEBHOOK_URL` if set.

## §5 GET /metrics contract (`app/services/metrics.py`)
Params: `date_from`, `date_to` (required); `breakdown` (default `none`);
`platform[]`, `account_id[]`, `campaign_id[]`; `group_by[]` from
`{platform, account, campaign, adset, ad, date, country, device}`; `sort`
(prefix `-` for descending); `page`, `page_size` (max 1000).
Response: `{data: [{...group keys, spend, impressions, clicks, conversions,
conversion_value, video_views, leads, purchases, ctr, cpc, cpm, cpa, roas}],
page, page_size, total}`. `total` is the number of groups (1 when ungrouped).
Derived metrics are null when their denominator is zero.

## §6 App tables (schema `app`, `app/models/app_models.py`)
    users(id, email UNIQUE, password_hash, created_at)
    connections(id, user_id, platform, name, encrypted_tokens BYTEA, status,
                created_at)
    schedules(id, connection_id, cadence, hour_utc, breakdowns JSONB,
              restatement_days, backfill_days, enabled, last_enqueued_at)
    sync_jobs(id, connection_id, kind, date_from, date_to, breakdown, status,
              stats JSONB, error, created_at, started_at, finished_at)
    sync_logs(id, sync_job_id, ts, level, event, data JSONB)
    -- api_keys, saved_reports arrive in Phase 7

`cadence`: `hourly | every_3h | daily | weekly | manual`.
`status` on connections: `active | needs_reauth | disabled`.

## §7 Conventions
- Env vars are prefixed `MAJRA_` (pydantic-settings). Secrets never in code or
  logs.
- Tokens encrypted at rest with Fernet (`MAJRA_ENCRYPTION_KEY`).
- Postgres schemas: `app` (operational), `raw` (append-only), `norm`
  (warehouse). Created by the initial migration.
- All timestamps UTC except fact `date` (see §3).
- Auth: email + password, bcrypt; JWT access (15 min) + refresh (30 days);
  tokens carry a `typ` claim (`access | refresh | oauth_state`) that is checked.
- Every connector ships fixture-based normalization tests.
- Structured JSON logs (structlog); `sync_job_id` on worker logs.
- The initial Alembic migration calls the shared `init_db()` bootstrap so the
  test schema (created via `create_all`) and the migrated schema cannot drift.
  Later migrations are normal Alembic operations.

## §8 Status
Built and tested (8 passing tests): auth, token encryption, the connector SDK,
the full Meta Ads connector (OAuth + long-lived tokens + paginated insights +
normalization), the idempotent sync engine, watermark/backfill logic, schedule
ticking, and the metrics API. Not yet built: Google Ads and the other connectors
(Phase 4–5), the dashboard (Phase 6), exports/API keys/saved reports (Phase 7),
and production hardening/monitoring (Phase 8).
