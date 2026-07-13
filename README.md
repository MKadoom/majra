# majra (مجرى)

A self-hosted marketing-data platform — an open-source alternative to Windsor.ai.
Connect your ad platforms, pull daily metrics into a normalized warehouse, and
query a single blended `/metrics` API across all of them.

**All eight phases are complete.** What's here is real, running, tested code —
not scaffolding. See `docs/INSTALL.md` to go from a bare Ubuntu VPS to a working
dashboard over HTTPS.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Skeleton: auth (JWT), Postgres schemas, config, health, Docker | ✅ built + tested |
| 2 | Connector SDK + full **Meta Ads** connector (OAuth → insights → warehouse) | ✅ built + tested |
| 3 | Celery scheduling, incremental sync, restatement, retries, idempotency | ✅ built + tested |
| 4 | Google Ads connector (proves the plugin architecture) | ✅ built + tested |
| 5 | GA4 ✅ · TikTok ✅ · Snapchat ✅ · FB Pages + Instagram ✅ · GTM ✅ | ✅ complete |
| 6 | Next.js dashboard | ✅ built + builds clean |
| 7 | API keys · CSV/Excel exports · saved reports · Google Sheets | ✅ built + tested |
| 8 | Hardening · Prometheus/Grafana · CI · production compose | ✅ built + tested |

**52 tests pass** covering auth, token encryption, the connector SDK, all seven
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

**The connector layer (Phases 2–5) is complete**, and there is now a dashboard.

## The dashboard

Next.js (App Router) + TypeScript + Tailwind + Recharts, at `frontend/`. Four
pages: **Explore** (all four datasets — Ads, Web, Social, Tags — with KPIs, a
time-series chart, a group-by breakdown table and CSV download), **Connections**,
**Sync jobs** (with step-by-step logs), and **Settings**.

Two things worth knowing about how it's built:

- **No token ever reaches client-side JavaScript.** Both the access and refresh
  tokens live in httpOnly cookies. Every browser call goes through a server-side
  proxy at `/api/proxy/*`, which attaches the access token, and — if the backend
  says it expired — refreshes it, retries, and rewrites the cookie, all without
  the page knowing. As a side effect the backend needs no CORS at all.
- **Colour belongs to the data.** The chrome is deliberately quiet; each platform
  is a "tributary" with one hue used consistently in every chart, dot and legend.
  Numbers are monospaced with tabular figures so columns align and digits don't
  jitter. The signature element is the **channel strip** on each connection: one
  tick per day for the last two weeks, lit where data landed and dark where it
  didn't — so a broken connector reads as a hole in the channel without anyone
  opening a log.

## Arabic / العربية

The dashboard ships in English and Arabic. One toggle in the sidebar sets a
cookie; the root layout reads it and sets `dir="rtl"` on `<html>` — and because
every component uses logical CSS properties (`border-inline-end`, `ms-*`,
`text-start`) and never `left`/`right`, that single attribute mirrors the entire
interface. The chart mirrors too: the value axis moves to the right and time runs
right-to-left, so it doesn't contradict the page it sits in.

**Numbers stay in Western digits.** Plain `ar-SA` would render `12,345.67` as
`١٢٬٣٤٥٫٦٧`, and browser ICU may default it to the Hijri calendar. Arabic-Indic
numerals are right in prose, but Gulf business and financial reporting uses
Western digits, and ad platforms report Gregorian days — a spend figure and a
campaign date must read the same in both languages. So the locale is pinned to
`ar-SA-u-nu-latn-ca-gregory`: Arabic text, Western numerals, Gregorian dates.

## Getting the data out

- **Read-only API keys.** Create one in Settings and pull your numbers from a
  script: `curl -H "Authorization: Bearer mk_…" ".../metrics?date_from=…"`. A key
  is read-only *by construction*, not by a scope check — write endpoints simply
  don't accept the key principal, so a leaked key can't start a sync, add a
  connection, or mint another key.
- **Exports.** `GET /exports?dataset=ads&format=csv|json|xlsx|parquet` takes the
  same filters as `/metrics` and streams a file. (Parquet needs `pip install
  pyarrow`; the other three work out of the box.)
- **Saved reports.** A query that runs on a schedule and delivers itself — into a
  **Google Sheet**, to a **webhook** (HMAC-SHA256 signed so you can verify it came
  from your majra), or to your **inbox** as a CSV attachment. The window rolls:
  "the last 30 days" stays true every morning instead of freezing on the day you
  created it.

## See it working first

You don't need a single developer account to look at the whole thing:

```bash
make up
docker compose -f infra/docker-compose.yml exec backend python scripts/seed_demo.py
# then sign in at http://localhost:3000 as demo@majra.dev / demo-password
```

That seeds 60 days of plausible blended data across four ad platforms, GA4 and
social. TikTok is missing two days **on purpose** — look at the channel strips on
the Connections page and you'll see the gap. That's what a broken connector looks
like, so you'll recognize it when it's real.

## Running it for real

`docs/INSTALL.md` takes a bare Ubuntu 24.04 server to HTTPS in eight steps: Caddy
gets certificates automatically, Postgres/Redis/the API have **no published
ports** (the dashboard reaches the API over the internal network), migrations run
on boot, and a nightly `pg_dump` rotates on 14 days.

`docs/OPERATIONS.md` is the runbook — restore-testing, key rotation, what to do
when a platform revokes a token, and why yesterday's conversions changed
overnight. `docs/CONNECTOR_SDK.md` is how to add a platform in a day.

Hardening: per-IP/per-key rate limits (Redis-backed when you run more than one
worker — an in-process limit with 4 workers lets 4× the traffic through), security
headers, CORS closed by default, and Prometheus at **`/_metrics`** — *not*
`/metrics`, which is the ad-performance query API. A Grafana dashboard is
provisioned.

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
# Dashboard: http://localhost:3000
# API:       http://localhost:8000
# API docs:  http://localhost:8000/docs
```

Open the dashboard, create an account, and add a connection. (For local
development without Docker: `make web` runs the dashboard against a backend on
`localhost:8000`.)

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
