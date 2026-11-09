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
        needs_refresh(tokens, now=None) -> bool     # default: has a refresh
                                                     # token and expires within
                                                     # `refresh_ahead` (10 min)
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
(retryable: rate limit, 5xx, timeout), `AuthError` (a `PermanentError` subclass:
the credential itself is expired, revoked or rejected — HTTP 401, Meta codes
102/190, TikTok 40100/40105/40001, a token endpoint rejecting the grant), or
`PermanentError` (anything else not retryable, e.g. no permission on one
account). Nothing else escapes a connector.

Token lifecycle: before each sync the engine calls `refresh()` when
`needs_refresh()` says so and saves the result, Fernet-encrypted, on the
connection (keeping the old refresh token / `extra` if the platform didn't return
new ones). `AuthError` is never treated as a per-account skip: it fails the job
and sets the connection to `needs_reauth`, and the scheduler stops enqueueing
`needs_reauth` connections until they are reconnected. Reconnecting —
`GET /connect/{platform}?connection_id=…` (OAuth) or
`POST /connections/service-account` with `connection_id` — replaces that
connection's tokens in place and resets it to `active`, keeping its id,
schedules, account allow-list and history. `get_connector(platform)` returns an
instance; unknown platform raises `PermanentError`.

Breakdown sets a connector may support (`BREAKDOWN_SETS`): `none`, `country`,
`device`, `age_gender`. `none` is mandatory. A connector may support a subset and
raise `PermanentError` for the rest (the Google connector supports `none`,
`country`, `device`).

A connector is a self-contained drop-in under `app/connectors/<slug>/` and needs
no edits to `app/core` (and usually none to `app/api`):
- It declares `raw_model` (a class attribute pointing at its append-only raw
  table, or `None`); the sync engine reads `connector.raw_model` — there is no
  central registry to edit.
- It declares `dataset` — which fact shape it feeds. The engine dispatches on
  this (`app.services.sync.DATASET_HANDLERS`): `ad_performance` (Meta, Google,
  TikTok, Snapchat), `web_performance` (GA4), `social_performance` (FB Pages +
  Instagram), or `config_snapshot` (GTM). Reusing an existing shape needs no
  engine change; a genuinely new shape adds one handler. Config connectors yield
  records from `snapshot()` instead of `fetch_insights()`, and their handler
  APPENDS a captured_at-stamped snapshot rather than delete+insert.
- It declares `auth_kind` — `oauth` (browser flow via `/connect/{platform}`) or
  `service_account` (credentials posted to `/connections/service-account`, then
  `credentials_from_input` builds the stored token blob). GA4 is the first
  service-account connector; that new auth mode is the one thing that required
  new `app/api` surface.
- It may carry its own settings. Meta reads the shared `Settings`
  (`app/core/config.py`); the Google connector defines a connector-scoped
  `GoogleAdsSettings` inside its own package (same `MAJRA_` env prefix). GA4
  needs no env config — its credentials live per-connection.
The raw-table model itself lives in `app.models` so it registers in the metadata
for migrations.

## §3 Normalized schema (schema `norm`)
Dimensions (`app/models/norm_models.py`):

    accounts(id, connection_id, platform, external_id, name, currency, timezone,
             platform_override?, UNIQUE(platform, external_id))
    -- platform_override relabels an account's FACTS under a different platform
    -- than its connection (e.g. a WhatsApp Marketing Messages account the Meta
    -- Marketing API surfaces → tag "whatsapp" so it reports separately). Set via
    -- PATCH /accounts/{id}/platform; applied on the next sync.
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
  row. Blended cross-currency totals: `/metrics?currency=USD` folds each row's
  spend/value by `rate_to_usd[row]/rate_to_usd[reporting]` from the `app.fx_rates`
  table (owner-supplied via `PUT /fx-rates`; USD implicitly 1.0). Without the
  param, raw per-currency sums are returned unchanged.
- `date` is the platform-reported day in the account's own timezone — that is
  how every platform reports dailies. Do not convert.
- Month partitioning of the fact table is a v2 migration; not needed at current
  volumes.

Other facts:
- `fact_web_performance` (GA4) — built. Columns: `date, account_id,
  source, medium, campaign_name, sessions, users, conversions, revenue`.
  Dimensioned by traffic source/medium/campaign (strings, no ad hierarchy); no
  `breakdown` column. Idempotency via delete+insert scoped to (account, date
  range). Revenue is in the property's currency (ISO mapping is a v2 concern).
  Queried via `GET /web-metrics` (group by date/source/medium/campaign/account).
- `fact_social_performance` (Facebook Pages + Instagram) — built. Columns:
  `date, account_id, grain, external_ref, impressions, reach, engagement,
  followers`. `grain` is page | ig_user | post; `followers` is an as-of-date
  snapshot (the `/social-metrics` query maxes it, the daily flows are summed).
  Idempotency via delete+insert scoped to (account, date range).
  Source metrics (Graph v26.0, T8): page `impressions` = `page_impressions`,
  `reach` = `page_total_media_view_unique` (replaces `page_impressions_unique`,
  retired in v26), `engagement` = `page_post_engagements`, `followers` =
  `page_fans`; Instagram `reach`, `followers` = `follower_count`, and
  `impressions` = daily `views` (metric_type=total_value — IG `impressions`
  was retired 2025-04-21). If Meta rejects a metric the others are fetched one
  by one and an `api.version` alert is raised. Reach/impressions are therefore
  "media views" from these dates on, not comparable 1:1 with older rows.
- `gtm_resources` (GTM) — built. A config snapshot, not daily metrics:
  `account_id, kind (container|workspace|tag|trigger|variable), container_ref,
  workspace_ref, external_id, name, type, data JSONB, captured_at`. Each sync
  APPENDS a full snapshot under one `captured_at` (audit history); the current
  config is the rows at the latest captured_at. Read via `GET /gtm-resources`.

Raw layer (schema `raw`): one append-only table per platform+endpoint —
`meta_insights(id, connection_id, account_external_id, params jsonb,
payload jsonb, fetched_at)`. Raw is the re-normalization source of truth and is
never mutated.

## §4 Sync semantics (`app/services/sync.py`, `app/services/schedules.py`)
- Watermarks are **per account**: `app.account_sync_state(account_id,
  breakdown, synced_from, synced_to, backfill_completed_at)` holds the
  contiguous range each account has synced successfully. A successful job
  extends it only when the job's range touches or overlaps it (never across a
  gap); a skipped account doesn't advance. An account the platform answered for
  with no rows *does* advance (no activity is still "synced"). The connection's
  watermark is the **oldest** `synced_to` among its (allow-listed) accounts that
  have coverage; accounts that never synced don't hold it back. With no
  coverage yet it falls back to `max(date)` of the dataset's fact table (the
  fact-derived `watermark()` also still drives `/freshness`). Migration 0016
  seeds coverage from existing facts with the backfill marked complete.
- A scheduled run fetches `[watermark - restatement_days, yesterday]`, never
  earlier than `yesterday - backfill_days`, split into ≤30-day jobs;
  `restatement_days` default 7 (platforms restate conversions retroactively).
  Each tick stamps `backfill_completed_at` on accounts whose `synced_from` has
  reached that horizon.
- Write path (`run_sync`): append raw pages, upsert dims, then **delete+insert**
  facts scoped to (this connection's accounts, this breakdown, this date range)
  in one transaction → every sync is idempotent and safe to retry. That
  transaction holds `pg_advisory_xact_lock(7201, connection_id)`, so syncs of
  one connection are serialized (an overlapping sync waits, then replaces the
  first one's rows instead of doubling them; after `lock_timeout` 10 min it
  fails with a retryable `TransientError`). A row dated outside the job's range
  is kept in raw but never written to facts (it would escape the delete scope),
  and an account that errors mid-fetch contributes no rows at all (its existing
  facts stay untouched). **An account the platform answers with zero rows for a
  range that already holds facts keeps those facts** (`stats.accounts_empty_kept`)
  — an empty answer is never allowed to wipe stored data.
- Dim resolution derives an ad's campaign/ad-set from the entity hierarchy when
  an insight row carries only the ad id. Meta and Google report full ids; TikTok
  and Snapchat report at ad level only, so their facts still get campaign_id and
  adset_id populated (via the ad→adset→campaign map built during entity sync).
- First run after connect (no watermark): backfill `backfill_days` (default 365)
  in month-sized chunks, one `sync_job` per chunk, oldest first.
- Time is injectable: `incremental_range(..., today=)` and
  `backfill_chunks(..., today=)` accept a reference date so the scheduler is
  deterministic and testable; they default to the wall clock.
- Job kinds: `manual | scheduled | backfill`. Retries (Celery): autoretry up to
  5× with exponential backoff on `TransientError`; `PermanentError` fails the job
  immediately with `error = "ClassName: message"`. On final failure the worker
  raises a `sync.failed` alert (`app/services/alerts.py`: WhatsApp and/or the
  `MAJRA_ALERT_WEBHOOK_URL` JSON POST). A successful job whose stats show
  `accounts_skipped > 0` (ids in `accounts_skipped_ids`) or `api_warnings`
  raises `accounts.skipped` / `api.version`. A daily beat task alerts on tokens
  expiring within 14 days and pinned API versions within 90 days of sunset, and
  sends a digest. `GET /health` reports `alerts_configured`.

## §5 GET /metrics contract (`app/services/metrics.py`)
Params: `date_from`, `date_to` (required); `breakdown` (default `none`);
`platform[]`, `account_id[]`, `campaign_id[]`; `group_by[]` from
`{platform, account, campaign, adset, ad, date, country, device}`; `sort`
(prefix `-` for descending); `page`, `page_size` (max 1000).
`currency` (default `SAR`, see §5a; unknown → 422); `group_by` also accepts
`currency` (native, unconverted per-currency rows).
Response: `{data: [{...group keys, spend, impressions, clicks, conversions,
purchase_value, conversion_value (alias), video_views, leads, purchases,
messages_sent, messages_delivered, ctr, cpc, cpm, cpa, roas}], page,
page_size, total, currency, warnings[], purchase_overlap}`. `total` is the
number of groups (1 when ungrouped). Derived metrics are null when their
denominator is zero.

**Cross-platform purchase overlap.** Each platform counts the purchases
(donations) it attributes to itself, so one donation can be counted once per
platform; a sum across platforms is never a de-duplicated count. When
`platform` is not in `group_by` and more than one platform in scope (after
every filter, including `platform[]`) has `purchases > 0`, the response sets
`purchase_overlap = {platforms[], metrics[] (purchases, purchase_value,
conversion_value, cpa, roas), note}` and appends `note` to `warnings[]`;
otherwise `purchase_overlap` is null. Explore shows it as a translated note
under the KPI cards; `GET /exports?total=true` (ads) adds a `note` column
whose only value is on the Total row (the Total row spans platforms in every
grouping); the MCP `get_ad_metrics` tool returns the field as-is.

## §5a Metric semantics and currency (T5 — approved 2026-11-03)

**Reporting currency**
1. Every monetary figure the API returns is in ONE currency, named in the
   response (`currency`). The default is **SAR** — for `/metrics`, `/v1/*`,
   exports, saved reports, pacing, the digest and the dashboard. `currency=XXX`
   picks another. Raw, unconverted sums across currencies are never returned;
   the only unconverted view is `group_by=currency` (one row per currency).
2. Conversion uses `fx_rates` (`rate_to_usd`); SAR→USD is pinned at the peg
   (1 USD = 3.75 SAR) rather than the provider's quote.
3. A currency with **no FX rate is excluded** from monetary sums (never passed
   through at 1.0), and the response lists it in `warnings[]`
   (`"no FX rate for XYZ — its spend is excluded"`).

**Metric definitions (cross-platform)**
| field | meaning | per platform |
|---|---|---|
| `spend` | money paid to the platform; provenance in `cost_source` | WhatsApp (T6): Meta `pricing_analytics` cost per Riyadh day (`pricing_analytics`); if Meta returns none, delivered × `MAJRA_WHATSAPP_PRICE_PER_DELIVERED` (`estimate`), or 0 if that price isn't in the account's currency (`missing`). Ad platforms: `platform` |
| `purchases` | completed purchases/donations the platform attributes | Meta `omni_purchase`, TikTok `complete_payment`, Snapchat `conversion_purchases`; Google 0 until segmented; **WhatsApp 0** |
| `purchase_value` *(API name; stored in the `conversion_value` column, which the API also still returns as an alias for one release)* | value of those purchases | same sources |
| `roas` | `purchase_value / spend` | null when spend = 0 |
| `conversions` | the platform's own optimisation-event count — **not comparable across platforms**, not used in ROAS or CPA | Meta purchases+leads, TikTok `conversion`, Snapchat purchases, Google all conversions |
| `cpa` | `spend / purchases` (was spend / conversions) | |
| `messages_sent`, `messages_delivered` *(new)* | WhatsApp marketing messages | WhatsApp only; its `impressions` become 0 so CPM isn't diluted |

**Coverage**
4. New `app.metric_coverage(platform, metric, valid_from, note)`. Where a
   request reaches before `valid_from` (e.g. TikTok `purchases` before the
   purchase field was pulled), the API returns a `warnings[]` entry and the
   dashboard shows it. Migration 0017 seeds TikTok purchases with the first
   date that has `purchases > 0` — read from the data, not assumed.
   Migration 0017 also moves existing WhatsApp-connector rows' `purchases` →
   `messages_sent` and `impressions` → `messages_delivered` (Meta accounts
   relabelled `whatsapp` via `platform_override` keep their real purchases).

**Tracking changes** — `app.tracking_changes(changed_on, platform, metric,
note)` (migration 0022) records days a metric's *meaning* changed: pixel
routing, deduplication or sender changes. `platform` NULL means every
platform. When a request range straddles one (`date_from < changed_on <=
date_to`, platform filter honoured), `/metrics` (and so `/donations/compare`)
adds a `warnings[]` entry: days before and after are not like for like.
`GET /tracking-changes` lists them; `POST` / `DELETE /tracking-changes` are
owner-only. 0022 seeds the 2026-11-09 clean-up: Google Ads Purchase deduped by
transaction ID (GTM v17); Stape Snapchat gateway removed (Jood Snap CAPI is the
single server sender); Jood "pixel events only for ad-link visits" on for
Meta, Snapchat and TikTok (≈13:40 Riyadh).

**Spend provenance (T6)** — `fact_ad_performance.cost_source`
(`platform | pricing_analytics | estimate | missing`, migration 0018; pre-T6
WhatsApp rows are marked `estimate` until re-synced). `/metrics` adds a
`warnings[]` entry for every platform with `estimate`/`missing` days in range,
and accepts `group_by=cost_source`. WhatsApp days are **Riyadh days** (UTC+3):
messaging and pricing analytics are pulled at HALF_HOUR granularity over a
window aligned to Riyadh midnight and summed per Riyadh date (DAILY — Meta's
own day boundaries — only if HALF_HOUR is refused).

**Acceptance**
5. With production data, Oct 2–31 2026 Explore (default view) must show spend
   in SAR matching the per-platform native totals converted at the stored
   rates, and ROAS = purchase_value / spend — the review's reference figures
   are 120,313 SAR and 1.42 (to be re-checked against the live API after deploy).

## §5c Meta Graph API version (T8)
All Meta surfaces — ads (`meta`), Pages/Instagram (`meta_social`), WhatsApp
analytics and the WhatsApp alert sender — call **Graph v26.0** (v21.0 is
retired 2027-01-21). `python -m scripts.meta_parity --connection-id N
[--month 2026-02] [--versions v21.0,v26.0]` (#17) checks, read-only, that a
closed month's totals are identical across versions, both through the
connector's own fetch + normalize path and an account-level cross-check, and
reports renamed or missing fields and action types. Exit 0 = parity, 1 = drift,
2 = could not run. The February 2026 reference (86,914.24 spend / 1,629
purchases / 177,721.76 purchase value) is printed for information. Run on
connection 12 on 2026-11-04: PASS on every metric.

## §5b Reconciliation and contract fixtures (T7)
- `BaseConnector.fetch_account_totals(tokens, account, date_from, date_to)` —
  optional; returns the platform's OWN account-level daily `{spend,
  impressions, purchases}` (Meta `level=account`, Snapchat account stats), or
  `None` = unsupported (TikTok, Google Ads, GA4, social, WhatsApp today).
- `app/services/reconciliation.py`, daily beat task (45 min before the
  digest): for active, non-deleted, allow-listed accounts, compare those totals
  with stored facts (breakdown `none`, native currency) for the 7 days ending
  two days ago. Tolerance: spend max(1 unit, 1%), impressions max(10, 1%),
  purchases max(1, 2%). Every check is stored in `app.reconciliation_results`
  (migration 0019); mismatches raise one `data.mismatch` alert; platform errors
  are recorded, not alerted. `GET /reconciliation` (also `/v1`) = latest run;
  `POST /reconciliation/run` runs it now.
- Contract fixtures: `backend/tests/fixtures/contracts/<platform>/*.json`
  (`synthetic-*` from documented shapes, `recorded-*` from
  `python -m scripts.record_contracts --connection-id N --date YYYY-MM-DD`,
  read-only, tokens/secrets/names redacted). `tests/test_contracts.py` replays
  every file through the real connector and asserts the normalized contract
  and that ad-level rows add up to the account totals.

## §5d Jood donations: ground truth vs platform claims
- `app.donation_orders` (migration 0020) holds each real donation/order from the Jood store
  export, once: `user_id, source, external_id (unique per user), ordered_at, date (Asia/Riyadh
  day), amount, currency, status, is_paid, platform, utm_*, link, raw`.
- `POST /donations/import` (JWT; JSON body `{filename, content | content_base64, mapping?,
  link_platforms?, default_currency, dry_run=true}`): CSV text or base64 CSV/XLSX.
  - Arabic and English headers are auto-detected, and `mapping` overrides them.
  - Arabic-Indic digits and "ر.س"/"SAR" amounts are parsed. Naive dates are read as Riyadh time.
  - Only paid/completed statuses count; no status means paid.
  - Platform attribution: an explicit platform column, then `link_platforms` (substring of the
    Jood per-platform ad link → platform), then `utm_source`, else `unattributed`.
  - A dry run previews the mapping, statuses, skipped rows and per-platform counts, and writes
    nothing. Re-import upserts by order id (idempotent).
- `GET /donations`, `GET /donations/orders` and `GET /donations/compare` are read endpoints
  (also on `/v1`). `compare` puts each platform's claimed purchases and purchase value (§5a,
  same reporting currency) next to the paid Jood orders attributed to it, with:
  - `claim_ratio` = claimed ÷ real;
  - `cost_per_jood_donation`;
  - `jood_roas`;
  - totals, including over-claim across all platforms.

  It is evidence, not a correction: each platform's Ads Manager keeps its own number.
- **GA4 is the automatic reference** (no upload needed). The GA4 connector also pulls
  `ecommercePurchases` into `fact_web_performance.purchases` (migration 0021).
  - `compare` adds `ga4_purchases`, `ga4_revenue` and `ga4_ratio` (claimed ÷ GA4) per platform.
    The platform comes from the session source/medium: social sources with any medium; Google
    only with a paid medium.
  - Totals add `ga4_purchases` and `overclaim_vs_ga4`.
  - The daily digest lists claims vs GA4 for the last 7 days and flags any platform claiming
    more than 1.5× what GA4 saw.
- **Personal data is never stored.** Columns whose header looks like a name, phone, e-mail,
  address, ID or bank account are dropped from `raw` (e.g. Jood's اسم المتبرع، رقم الجوال،
  الحساب البنكي).

## §6 App tables (schema `app`, `app/models/app_models.py`)
    users(id, email UNIQUE, password_hash, created_at)
    connections(id, user_id, platform, name, encrypted_tokens BYTEA, status,
                synced_account_ids JSONB?, deleted_at?, created_at)
    -- deleted_at = soft delete (recycle bin): hidden from listings, schedules
    -- skip it, and its facts drop out of metrics; restorable via
    -- POST /connections/{id}/restore. Nothing is destroyed.
    schedules(id, connection_id, cadence, hour_utc, breakdowns JSONB,
              restatement_days, backfill_days, enabled, last_enqueued_at)
    sync_jobs(id, connection_id, kind, date_from, date_to, breakdown, status,
              stats JSONB, error, created_at, started_at, finished_at)
    sync_logs(id, sync_job_id, ts, level, event, data JSONB)
    api_keys(id, user_id, name, prefix UNIQUE, key_hash, scopes JSONB,
             created_at, last_used_at, revoked_at)
    saved_reports(id, user_id, name, dataset, params JSONB, destination JSONB,
                  cadence, hour_utc, enabled, last_run_at, last_status, last_error)
    fx_rates(id, currency UNIQUE, rate_to_usd NUMERIC, updated_at)
    budgets(id, user_id, name, platform?, account_id?, amount_monthly NUMERIC,
            currency, enabled, created_at)
    saved_views(id, user_id, name, config JSONB, created_at)  -- named Explore state

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

## §7a API keys, exports, saved reports (Phase 7)
- **API keys** are `mk_…` bearer tokens. High-entropy random, so a single SHA-256
  is the right hash (bcrypt would tax every request for no gain). The plaintext is
  shown once; only `prefix` (non-secret head, indexed) and `key_hash` are stored.
- **Read-only is structural, not a scope check.** Read endpoints depend on
  `get_read_principal` (JWT *or* key); every write endpoint depends on
  `get_current_user` (JWT only). A key therefore cannot sync, connect, or mint
  another key even if its scopes were tampered with.
- **Exports**: `GET /exports?dataset=ads|web|social&format=csv|json|xlsx|parquet`,
  same filters as the matching query endpoint. CSV streams; xlsx/parquet build in
  memory, hence the `MAJRA_EXPORT_MAX_ROWS` cap. Parquet is an optional extra
  (pyarrow); without it the endpoint returns a clear error.
- **Saved reports** store a RELATIVE window (`last_days`), because a scheduled
  report means "the last 30 days", not a range frozen at creation. Destinations:
  `google_sheets` (clear tab, then write — so a shorter result leaves no stale
  rows), `webhook` (HMAC-SHA256 in `X-Majra-Signature`), `email` (CSV attachment
  over SMTP), and `slack` / `teams` (a compact summary posted to an
  incoming-webhook URL). Celery beat runs due reports every 10 minutes. Secrets and service
  accounts are never echoed back by the API.
- **Report `params`** (a free JSONB dict, same keys the query endpoints take) also
  carries two delivery-shaping options, both flowing through `run_dataset_query`:
  - `currency` (ads only) — a reporting currency; spend/value are converted via
    `app.fx_rates` exactly like the Explore selector, so a blended report reads in
    one unit. Omit to leave each account in its own currency.
  - `total` (default `true`) — append a summary Total row (additive columns summed,
    the five ad ratios recomputed from the sums, never averaged). Sheets and the
    email CSV get it as a trailing row; the webhook payload carries it as a
    separate `total` field (plus a top-level `currency`) so `rows` stays clean for
    parsers; the chat summary pins it under the table.
  `cadence` is `daily | weekly` for reports (the schedule table's wider set does
  not apply here).
- The three query services no longer clamp `page_size` internally — the 1000-row
  cap lives at the HTTP boundary (`Query(le=1000)`), so exports can legitimately
  ask for far more without being silently truncated.

## §7b Dashboard (Phase 6)
Next.js app in `frontend/`. It talks to the backend only through its own
server-side proxy (`/api/proxy/*`), which holds the access + refresh tokens in
httpOnly cookies and refreshes them transparently — so no token is ever exposed
to client-side JavaScript and the backend requires no CORS. `GET /accounts` was
added to the API for the dashboard's filters and account display.

## §7c WhatsApp marketing messages
`whatsapp` connector (service-account auth, `dataset=ad_performance`,
`raw_model=None` so no migration). Reads the **WhatsApp Business Management API**:
`message_templates` (each template ≈ a marketing "campaign") + `template_analytics`
(DAILY, `template_ids` chunked by 10). Metrics are mapped onto the ad shape —
`delivered→impressions`, `clicked→clicks`, `read→conversions`, `sent→purchases`,
`spend=0`. Auth is a single System User token (`whatsapp_business_management`) in
`MAJRA_WHATSAPP_TOKEN`; a connection pins which WABAs to pull (`waba_ids` in
`tokens.extra`). `insights_carry_hierarchy=True` (template==campaign, no entity
walk). Distinct from WhatsApp-objective *ads* (which live in the Meta ad account
and can be tagged via `platform_override`).

## §8 Status
Built and tested (52 backend tests passing; the dashboard builds clean): auth, token encryption, the connector SDK,
and eight connectors — **Meta Ads, Google Ads, GA4, TikTok Ads, Snapchat Ads,
Facebook Pages + Instagram, GTM, WhatsApp marketing messages** — the idempotent sync engine (four dataset
shapes: ad, web, social, config, with ad-level parent derivation), dataset-aware
watermark/backfill, schedule ticking, the metrics / web-metrics / social-metrics
APIs plus `/gtm-resources`, and a blended cross-platform `/metrics` query. The
connector layer (Phases 2–5) is complete, the dashboard (Phase 6) is built, and
Phase 7 adds read-only API keys, exports (CSV/JSON/xlsx/parquet) and scheduled
saved reports (Google Sheets / signed webhook / email). Phase 8 adds hardening
(rate limiting keyed per API key or IP, security headers, CORS closed by default),
Prometheus at `/_metrics` with a provisioned Grafana dashboard, CI, a production
compose with Caddy TLS and nightly backups, and the install/operations/SDK docs.

**All eight phases are complete.**
