# majra — Project Status & Action Plan
_Compiled 2026-07-29 after a full end-to-end audit (code, GitHub, Railway, live dashboard, Meta/TikTok consoles)._

## TL;DR
**The project is not broken.** `MKadoom/majra` is complete, tested (52 passing), deployed on Railway, and healthy right now (backend logs show steady `200 OK`s and working token refresh). What's "missing" is **live data from two ad platforms**, and both gaps are gated on **external platform approvals** — not code, not deployment. No rebuild is required.

---

## 1. What is actually working
- **Production is up:** `https://frontend-production-d57f0.up.railway.app` — Explore, Connections, Sync jobs, Reports, Settings all load; auth + silent token refresh work.
- **Railway** (project `clever-happiness`) runs 6 services (frontend, backend, worker, beat, Postgres, Redis). Backend deploys `MKadoom/majra @ main` (currently `31b5f6a`) and runs `alembic upgrade head` on boot.
- **Snapchat**: fully connected and syncing (spend, impressions, conversions all present — e.g. 6,549.54 spend in the last window).
- **Meta**: connected and syncing daily (impressions, clicks, conversions, purchase value all flowing).
- **The codebase**: all 8 phases complete; connector SDK + 7 connectors; idempotent sync; blended `/metrics`; bilingual dashboard.

## 2. The two real gaps (both external, neither is a code bug)

### Gap A — Meta shows spend = 0
**Root cause (confirmed via sync logs):** the Meta app `majra` (App ID `1567098908090799`) is in **Development mode**, whose **"Marketing API Access Tier" = Limited**. Your active owned account `act_1217959466650120` is large enough that pulling it exceeds the dev-tier limit, so Meta returns `[17] User request limit reached` and majra **skips that account on every sync** (`account.skipped` in the logs). The only Meta account that fits under the limit is a read-only USD share (`act_26652237857710975`), whose `spend` Meta withholds for read-only access — hence impressions/conversions but 0 spend. Snapchat proves the pipeline is fine (its spend flows through the identical code path).

**Fix (owner: you — external, ~3–7 days):** Meta App Review to upgrade **Marketing API Access Tier → Standard**. The submission is already staged (includes the tier + `ads_read` + `ads_management`). It needs: business verification (Security Center), the usage descriptions (drafted for you below), a short screencast, submit, then **Publish** the app.

### Gap B — TikTok not connected
**State:** the TikTok Marketing API app **"Majra"** was created on `business-api.tiktok.com` (redirect + scopes set) and is **Pending** review. App ID/Secret are issued after approval.

**Fix (owner: you for approval; me for wiring):** once approved, I set `MAJRA_TIKTOK_APP_ID` / `MAJRA_TIKTOK_APP_SECRET` directly on the Railway backend (I now have Railway access), redeploy, and you authorize via `/connect/tiktok`. Everything after approval is automatic.

## 3. Optional polish from this session (NOT required for the project to work)
Committed locally (in your `majra-analytics` working copy) but not on the production repo `MKadoom/majra`:
- CI fix (run full pipeline on the default branch, lint whole repo, drop a redundant workflow)
- TikTok connector fail-fast when unconfigured (+ test)
- **Per-connection account allow-list** (`synced_account_ids` + migration 0008 + endpoint) — lets a connection sync only chosen accounts, conserving Meta's rate budget. *Note: even this may not beat the dev-tier limit on its own — App Review is the true fix.*
- Docs: TikTok runbook, corrected Meta root cause in the handoff.

These reach production only by pushing to `MKadoom/majra`. That needs your GitHub credentials (the connector is public-repo-only and can't access your private repos; I won't handle tokens in plaintext). **This is optional — the two gaps above do not depend on it.**

## 4. The plan, in priority order
1. **[You, biggest impact] Meta App Review** → fixes Meta spend + unblocks history backfill. Do business verification first (it's the long pole).
2. **[You] TikTok approval** → then I wire the env vars via Railway in minutes.
3. **[Me, automated] Daily tracker** already watches both app statuses + your Meta spend and will notify you the instant either clears.
4. **[Optional, you] Push this session's improvements** to `MKadoom/majra` when convenient.

## 5. Meta App Review — usage descriptions to paste
- **ads_read / Marketing API Access Tier:** "Majra is a self-hosted marketing analytics dashboard used internally by our organization. It uses ads_read and the Marketing API to pull daily performance metrics (spend, impressions, clicks, conversions, conversion value) for ad accounts we own or manage, shown in an internal reporting dashboard. We need the standard access tier because our owned ad account exceeds the development-tier rate limit, throttling our daily sync."
- **ads_management:** "Read-only in practice — used only to read campaign, ad set, and ad entities so reporting can group by campaign. We never create, edit, or pause ads."
- **business_management / pages_show_list / pages_read_engagement:** "Used to list owned ad accounts and pages for reporting selection and to read organic page insights in the same dashboard."

---
_Sources for the diagnosis: Railway service config + deploy logs, majra sync-job logs (`account.skipped: [17]`), the live `/metrics` API grouped by account, and the Meta Business/Developer consoles._
