# majra — orientation for coding sessions

**Read `docs/SPEC.md` before making any change.** It is the binding contract for
the schema, the connector interface, and sync semantics.

## What this is
A self-hosted marketing-data platform (an open-source Windsor.ai alternative):
connect ad platforms, pull daily metrics, normalize them into a warehouse, query
a blended `/metrics` API, and (later) view a dashboard.

## Stack
Python 3.12 · FastAPI · SQLAlchemy 2 + Alembic · Postgres 16 · Redis 7 · Celery
(worker + beat) · pytest + respx. Frontend (Phase 6) will be Next.js.

## Layout
    backend/app/core/        config, db bootstrap, security (JWT + Fernet)
    backend/app/models/      app_models (schema app), norm_models (norm + raw)
    backend/app/connectors/  base.py = the SDK; meta/ = first connector
    backend/app/services/    sync.py, schedules.py, metrics.py
    backend/app/workers/     tasks.py = Celery app, retrying sync task, beat tick
    backend/app/api/         auth.py, platform.py (OAuth), data.py (syncs/metrics)
    backend/alembic/         migrations (0001 = initial)
    backend/tests/           unit + end-to-end (mocked Graph API via respx)
    infra/docker-compose.yml the whole stack
    docs/SPEC.md             the contract

## Conventions (see SPEC §7)
- Env vars prefixed `MAJRA_`; secrets never logged.
- Three Postgres schemas: `app`, `raw`, `norm`.
- Add a connector by subclassing `BaseConnector` under `app/connectors/<slug>/`
  and nothing else — proven eight times now (Meta, Google Ads, GA4, TikTok,
  Snapchat, Facebook Pages + Instagram, GTM, WhatsApp marketing messages). A
  connector declares `raw_model`,
  `dataset` (`ad_performance` | `web_performance` | `social_performance` |
  `config_snapshot`), and `auth_kind` (`oauth` | `service_account`), and may
  carry its own settings. Reusing a dataset shape needs no engine edits; a new
  shape adds one handler in `app.services.sync.DATASET_HANDLERS`. Ad-level-only
  reporters (TikTok/Snapchat) get campaign/ad-set derived from the entity
  hierarchy automatically. If you think you need to change `app/core`, stop and
  say why first.
- Facts are written delete+insert (idempotent); never add a unique constraint to
  make upserts work.
- Run `python -m pytest -q` (from `backend/`, with `PYTHONPATH=.`) — keep it
  green. Every connector needs fixture-based normalization tests.

## Frontend (`frontend/`)
Next.js 14 App Router + TypeScript + Tailwind + Recharts. Auth is server-side:
both tokens sit in httpOnly cookies and every browser call goes through the proxy
at `src/app/api/proxy/[...path]/route.ts`, which attaches the access token and
silently refreshes on 401 — pages never handle tokens, and the backend needs no
CORS. Design rules: colour belongs to the data (one hue per platform, in
`PLATFORM_COLOR`), numbers use the `.num` class (mono + tabular), and layout is
RTL-safe — logical properties only, never `left`/`right`. Verify with
`npm run build` (it typechecks). UI is **shadcn/ui** themed to majra's own Tailwind
tokens (no parallel CSS-variable layer): components live in `src/components/ui/`
(button, card, input, badge, table, alert, select, dialog, …) and the barrel
`src/components/ui/index.tsx` also exports the shared primitives (Panel, Status,
Spinner, ErrorNote, PlatformDot, ChannelStrip, Empty) rebuilt on shadcn — so
`import { … } from "@/components/ui"` still resolves. `cn()` is in `src/lib/utils.ts`.
Tokens/theme live in `tailwind.config.ts` + `globals.css`; keep colour on the data.

## Running locally
`cp .env.example .env`, fill `MAJRA_ENCRYPTION_KEY` and `MAJRA_SECRET_KEY`, then
`make up`. API at http://localhost:8000, OpenAPI docs at `/docs`.
