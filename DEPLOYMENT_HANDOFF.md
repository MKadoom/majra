# Majra — Deployment Handoff

_Last updated: 2026-07-16_

This document captures everything set up during the deployment so you (or any
developer) have a complete record.

---

## 1. Live application

- **URL:** https://frontend-production-d57f0.up.railway.app
- **Sign in:** the account you created (`demo@majra.dev` was recreated as your
  working account after the demo data was wiped). You can create more accounts
  from the login page.
- **Pages:** Explore (metrics), Connections, Sync jobs, Reports, Settings.
- **Public legal pages:** `/privacy` and `/terms` (bilingual EN/AR).

## 2. Source code

- **Repo:** https://github.com/MKadoom/majra (private)
- **Branch:** `main` — Railway auto-deploys every push.
- Fixes committed during deployment:
  - Bump Next.js to 14.2.35 (security CVEs that blocked the first build).
  - Bind backend uvicorn to `::` (Railway private networking is IPv6-only).
  - Proxy `/connect/*` from frontend to backend (so OAuth callbacks resolve).
  - Snapchat: align stats start/end to the ad account timezone (Snap 400'd on UTC).
  - Add `/privacy` and `/terms` pages.

## 3. Hosting — Railway

Project **clever-happiness** (Hobby plan), 6 services:

| Service   | Role                                   | Public? |
|-----------|----------------------------------------|---------|
| frontend  | Next.js dashboard                      | ✅ yes  |
| backend   | FastAPI (`--host ::`, runs migrations) | no      |
| worker    | Celery worker (runs the syncs)         | no      |
| beat      | Celery beat (fires daily schedules)    | no      |
| Postgres  | managed database                       | no      |
| Redis     | managed broker / rate-limit store      | no      |

Secrets live in each service's **Variables** tab (backend + worker + beat share
the platform credentials; frontend only has `MAJRA_API_URL`).

> ⚠️ **`MAJRA_ENCRYPTION_KEY` is load-bearing** — it encrypts every stored
> platform token. Copy it (and `MAJRA_SECRET_KEY`) from Railway into a password
> manager. If it's ever lost, every platform connection must be re-authorized.

## 4. Platform apps (developer registrations)

| Platform  | App           | Status | Notes |
|-----------|---------------|--------|-------|
| Meta      | `majra` (App ID 1567098908090799) | ✅ created, connected | Still in **Development mode** → low API rate limits. Privacy/terms URLs set → ready for App Review. |
| Snapchat  | `majra` OAuth app under **جمعية الثريا للمكفوفين بجازان** org | ✅ created, connected, syncing | Client ID b84d46ac-85aa-431e-943b-5caf2190349a |
| Google Ads| —             | ⏸ blocked | Needs 2-Step Verification (MFA) enabled on your Google account before Google Cloud Console is accessible. |
| TikTok    | —             | ⏸ blocked | Needs the developer-registration form completed (was waiting on your work email). |

All secret values are stored only in Railway variables (not in this file).

## 5. Data connections & sync

- **Meta Ads** — connected, ACTIVE.
- **Snapchat Ads** — connected, ACTIVE, pulling the جمعية الثريا account
  (first sync: 960 rows, this-month metrics visible in Explore).
- **Daily auto-sync:** both platforms scheduled at **16:00 UTC (19:00 KSA)**,
  server-side via the `beat` service. No manual clicks needed — data refreshes
  every day. To change the time, edit the schedules' `hour_utc` (see §7).

## 6. Known constraint — Meta rate limit

Meta's app is in **Development mode**, which caps API volume. A single daily
sync fits under the cap, but rapid repeated "Sync now" clicks trigger
`[17] User request limit reached` (a rolling ~1-hour limit). The daily schedule
avoids this by design. **Permanent fix:** submit the Meta app for **App Review**
(standard `ads_read` access) — the privacy/terms URLs are already set, so what
remains is business verification + a short use-case description in the App
Review section of developers.facebook.com.

## 7. Operating notes

- **See data:** Explore page → pick date range → Ads/Web/Social tabs.
- **Manual sync:** Connections page → "Sync now" on a platform.
- **Change daily time:** in the Railway **backend** service → Console:
  ```python
  python - <<'PY'
  from sqlalchemy import select
  from app.core.db import SessionLocal
  from app.models.app_models import Schedule
  db = SessionLocal()
  for s in db.scalars(select(Schedule)).all():
      s.hour_utc = 3   # e.g. 03:00 UTC = 06:00 KSA morning report
  db.commit()
  print("updated")
  PY
  ```
- **Arabic UI:** toggle in the sidebar (RTL mirrors the whole interface).

## 7b. AI insights (MCP) — new

`mcp/` contains a read-only MCP server, majra's answer to Windsor.ai's MCP.
Point Claude/ChatGPT at your own instance and ask questions about live data
instead of exporting CSVs. Five tools: `get_ad_metrics`, `get_web_metrics`,
`get_social_metrics`, `list_accounts`, `get_tag_snapshot`. Setup in
`mcp/README.md`. It cannot mutate anything — every tool maps to an API-key
(read) endpoint.

Machines reach the API at `https://<your-domain>/v1/*` with
`Authorization: Bearer <api key>`; the browser app keeps using `/api/proxy`.
That `/v1` surface is also what a Looker Studio or Sheets connector would use.

## 8. What still needs you

1. **Google Ads** — enable 2-Step Verification at myaccount.google.com/security,
   then I can build the OAuth client + developer token.
2. **TikTok** — complete the developer registration (needs a working email +
   phone verification), then I can create the app.
3. **Meta App Review** — optional but recommended to remove the dev-mode limit.
4. **Back up** `MAJRA_ENCRYPTION_KEY` and `MAJRA_SECRET_KEY` to a password manager.
5. **Verify** this evening that the 16:00 UTC sync shows Meta as SUCCESS on the
   Sync jobs page.
