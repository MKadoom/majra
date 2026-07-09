# majra (مجرى)

A self-hosted marketing-data platform — an open-source alternative to Windsor.ai.
Connect your ad platforms, pull daily metrics into a normalized warehouse, and
query a single blended `/metrics` API across all of them.

This repository is being built **phase by phase**. What's here is real, running,
tested code — not scaffolding.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Skeleton: auth (JWT), Postgres schemas, config, health, Docker | ✅ built + tested |
| 2 | Connector SDK + full **Meta Ads** connector (OAuth → insights → warehouse) | ✅ built + tested |
| 3 | Celery scheduling, incremental sync, restatement, retries, idempotency | ✅ built + tested |
| 4 | Google Ads connector (proves the plugin architecture) | ✅ built + tested |
| 5 | GA4 ✅ · TikTok ✅ · Snapchat ✅ · FB Pages + Instagram ✅ · GTM ✅ | ✅ complete |
| 6 | Next.js dashboard | ▢ next |
| 7 | API keys · CSV/Excel exports · saved reports · Google Sheets | ▢ |
| 8 | Hardening · Prometheus/Grafana · CI · production compose | ▢ |

**32 tests pass** covering auth, token encryption, the connector SDK, all seven
connectors — Meta, Google Ads, GA4, TikTok, Snapchat, Facebook Pages +
Instagram, and **GTM** — with full end-to-end syncs against mocked platform APIs
(pagination, OAuth + service-account auth, nested-stats flattening, metric
pivoting, micro-currency, config-tree walks), idempotent re-sync, dataset-aware
watermark/backfill, schedule ticking, the metrics / web-metrics / social-metrics
APIs, `/gtm-resources`, and a blended cross-platform `/metrics` query. Every
connector after Meta slotted in behind the same SDK with **zero changes to
`app/core`**. See `docs/SPEC.md` for the binding contract.

Four data shapes now flow through one engine: **ad performance** (Meta, Google,
TikTok, Snapchat → `/metrics`), **web analytics** (GA4 → `/web-metrics`),
**organic social** (FB Pages + Instagram → `/social-metrics`), and **config
snapshots** (GTM → `/gtm-resources`, append-only audit history). The paid
platforms blend in a single `/metrics` query; ad-level-only reporters (TikTok,
Snapchat) get their campaign/ad-set derived from the entity hierarchy.

**The connector layer (Phases 2–5) is complete.** Next is the dashboard.

## The three things only you can do

Everything in this repo is code, and the code is written. But a data platform
has to reach *your* ad accounts, which means three steps no code can do for you:

1. **Register developer apps** (Phase 0). Create a Meta app (dev mode is enough
   to pull your own accounts — no review needed), and later Google/TikTok/
   Snapchat apps. This is ~30 minutes of clicking through developer consoles and
   accepting terms. The full checklist is in the build plan.
2. **Click "Authorize"** once per ad account. OAuth requires a human to approve
   access in the browser; majra can't log in as you.
3. **Provide a machine.** A small VPS (or your own PC for testing). `make up`
   does the rest.

Once those are done, syncing and everything else is automatic.

## Quickstart (local)

Prerequisites: Docker + Docker Compose.

```bash
cp .env.example .env
# set two secrets in .env:
#   MAJRA_SECRET_KEY     ->  openssl rand -hex 32
#   MAJRA_ENCRYPTION_KEY ->  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

make up                      # builds images, starts stack, runs migrations
# API:  http://localhost:8000
# Docs: http://localhost:8000/docs
```

Register and log in:

```bash
curl -X POST localhost:8000/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"your-password"}'
```

To actually connect Meta you need Phase 0 done and `MAJRA_META_APP_ID` /
`MAJRA_META_APP_SECRET` set, plus `MAJRA_PUBLIC_BASE_URL` pointing at a
browser-reachable URL (use a `cloudflared` or ngrok tunnel in local dev, because
Meta must redirect back to your callback). Then:

```
GET  /connect/meta            -> returns the Facebook consent URL (open it)
     ...authorize in browser...
POST /syncs                   -> {connection_id, date_from, date_to, breakdown}
GET  /metrics?date_from=...&date_to=...&group_by=platform,date
```

## Running the tests

```bash
make test
# or, without Docker, from backend/ with a local Postgres:
#   createdb majra_test && PYTHONPATH=. python -m pytest -q
```

## How it fits together

```
platform APIs ──connector──> raw.<platform>  ──normalize──> norm.fact_ad_performance
                                                                     │
  Celery beat ─ schedules ─ run_sync (delete+insert, idempotent) ────┘
                                                                     │
                                        GET /metrics (blended, derived KPIs)
```

- **Connectors** are plugins: subclass `BaseConnector`, drop it under
  `app/connectors/<slug>/`, and it self-registers. The Meta connector is the
  reference implementation.
- **Sync** appends raw payloads (the re-normalization source of truth), upserts
  dimensions, then replaces facts for its exact (accounts, breakdown, date-range)
  scope in one transaction — so any sync can be re-run safely.
- **Breakdowns** (`none`, `country`, `device`, `age_gender`) are stored as
  separate row-sets and never summed together; `/metrics` always filters to one.

Full detail: `docs/SPEC.md`. Orientation for editing: `CLAUDE.md`.

## License

Choose one before publishing (MIT or AGPL-3.0 are the usual picks for a
self-hosted tool like this).
