# Deploying majra to Railway

This is written so your part is mostly clicking. I've translated the stack into
Railway's model, pre-generated your secrets, and pre-empted the failure modes I
know about. Expect **20–30 minutes**, most of it waiting for builds.

Railway can run majra's full stack — long-running API, background workers, a real
database — which is exactly the kind of thing that doesn't fit on a static host.

---

## The shape of it

Railway doesn't run a `docker-compose.yml` directly. Each service becomes its own
Railway service in one project. majra's six containers translate like this:

| In the compose file | On Railway | Why |
|---|---|---|
| `postgres` | **Managed Postgres** (New → Database) | Automatic backups + a dashboard. Replaces the container *and* majra's backup job. |
| `redis` | **Managed Redis** (New → Database) | Same — managed, no volume to babysit. |
| `backend` | A service from this repo | Runs migrations on boot, then serves the API. |
| `worker` | A service from this repo | Runs the platform syncs. |
| `beat` | A service from this repo | Fires the daily schedule. |
| `frontend` | A service from this repo (public) | The dashboard. **The only public service.** |
| `caddy` | *nothing* | Railway does HTTPS for you. No reverse proxy needed. |
| `prometheus` / `grafana` | *skip for now* | Optional; add later. Nothing depends on them. |

So: **4 services built from your code + 2 managed databases.**

---

## Before you start

You need a GitHub account and a Railway account (railway.com — sign in with
GitHub). Railway's Hobby plan is $5/month and includes $5 of usage; majra at rest
sits comfortably inside that.

### Your secrets — generated, ready to paste

I made these for you just now. They're real, they're yours, and they are **not**
committed to the repo (that's deliberate — secrets never go in git). Keep them
somewhere safe like a password manager; you'll paste them into Railway in step 4.

```
MAJRA_SECRET_KEY=e9402da849889d64aab55bdc294e9f426bb8de53f01dd0531c3a46f75e0e3370
MAJRA_ENCRYPTION_KEY=doTjhkvdodA79AaWVSORz9QQPv0FPU76EiYkLGxtLXU=
```

⚠️ **`MAJRA_ENCRYPTION_KEY` is load-bearing.** It encrypts every platform token
majra stores. If you lose it, every connection has to be re-authorized. If you
ever change it, same thing. Save it now.

---

## 1. Put the code on GitHub

Railway deploys from a GitHub repo, so majra needs to live in one.

1. Unzip `majra-v1.1.zip` somewhere on your computer.
2. Create a new repository on GitHub — **private is fine** (Railway can read your
   private repos once you connect it). Call it `majra`.
3. Push the unzipped folder to it. If you have git installed:
   ```bash
   cd majra
   git init && git add -A && git commit -m "majra v1.1"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/majra.git
   git push -u origin main
   ```
   (If you'd rather not use the command line, GitHub Desktop does the same with
   buttons — "Add existing repository", then "Publish".)

## 2. Create the project and add the two databases

1. In Railway: **New Project → Empty Project**. Name it `majra`.
2. **New → Database → Add PostgreSQL.** Wait for it to go green.
3. **New → Database → Add Redis.** Wait for green.

That's your data layer done — both with automatic backups, nothing to configure.

> Leave the service names as **Postgres** and **Redis** (the defaults). The
> reference variables in the next step point at those exact names.

## 3. Add the four app services

Two ways. The drag-and-drop is faster if it works for you:

**Option A — drag the import file.** Drag `infra/railway/compose.railway.yml`
from the unzipped folder onto the project canvas. Railway auto-imports the four
services as staged changes. Review, then apply.

**Option B — add each by hand** (do this if the drag doesn't pick everything up).
For **each** of `backend`, `worker`, `beat`, `frontend`:

1. **New → GitHub Repo →** pick your `majra` repo.
2. Open the new service's **Settings → Build**:
   - **Root Directory:** `backend` for backend/worker/beat; `frontend` for
     frontend. *(This is what makes Railway find each Dockerfile. Getting it
     wrong is the usual cause of a build that can't find its Dockerfile.)*
3. **Settings → Deploy → Custom Start Command:**
   - **backend:** `sh -c "alembic upgrade head && uvicorn app.main:app --host :: --port 8000 --workers 2"`
   - **worker:** `celery -A app.workers.tasks.celery_app worker --loglevel=info --concurrency=4`
   - **beat:** `celery -A app.workers.tasks.celery_app beat --loglevel=info`
   - **frontend:** leave default (its Dockerfile already starts the server).

## 4. Set the environment variables

For **backend**, **worker**, and **beat**, open each service's **Variables** tab
and add these. The `${{ … }}` ones are Railway *reference variables* — type them
exactly; Railway resolves them to the live database URLs and keeps them in sync.

```
MAJRA_ENVIRONMENT=production
MAJRA_DATABASE_URL=${{ Postgres.DATABASE_URL }}
MAJRA_REDIS_URL=${{ Redis.REDIS_URL }}
MAJRA_SECRET_KEY=e9402da849889d64aab55bdc294e9f426bb8de53f01dd0531c3a46f75e0e3370
MAJRA_ENCRYPTION_KEY=doTjhkvdodA79AaWVSORz9QQPv0FPU76EiYkLGxtLXU=
```

On **backend only**, add two more:

```
MAJRA_RATE_LIMIT_STORAGE=${{ Redis.REDIS_URL }}
MAJRA_HSTS_ENABLED=true
```

> **You do not need to worry about the `postgresql://` vs `postgresql+psycopg://`
> driver problem.** Railway hands out a bare `postgresql://` URL, which normally
> crashes SQLAlchemy on boot — I added a normalizer to majra's config that fixes
> the prefix automatically, so the reference variable just works.

For **frontend**, open its **Variables** and add:

```
MAJRA_API_URL=http://backend.railway.internal:8000
```

That's Railway's private network — the dashboard talks to the backend internally,
and the backend never gets a public URL. (Leave `MAJRA_PUBLIC_BASE_URL` for
step 6; the frontend needs a domain first.)

## 5. Deploy

Trigger a deploy on each service (Railway usually starts automatically once
variables are set). Watch **Deploy Logs** per service:

- **backend** should log Alembic running the migrations, then uvicorn starting.
  The first boot creates all seven migrations from scratch.
- **worker** and **beat** should connect to Redis and idle, waiting for work.

If backend is healthy, the hard part is over.

## 6. Give the dashboard a URL

1. Open the **frontend** service → **Settings → Networking → Generate Domain.**
   You'll get something like `majra-production.up.railway.app`.
2. Now close the loop so OAuth callbacks work. On **backend**, **worker**, and
   **beat**, add one more variable — your new domain:
   ```
   MAJRA_PUBLIC_BASE_URL=https://majra-production.up.railway.app
   ```
3. The services redeploy. Open the domain — you should get the majra sign-in
   page over HTTPS.

## 7. See it working

Open the **backend** service → the **⋯** menu → a shell/exec, and run:

```bash
python scripts/seed_demo.py --days 60
```

Then sign in at your domain with **demo@majra.dev** / **demo-password**. You'll
see the whole dashboard on live data — try the Arabic toggle too. TikTok is
missing two days on purpose; the channel strips on the Connections page show the
gap.

When you're ready for real data, wipe the demo and make your own account:

```bash
python scripts/seed_demo.py --reset --days 0
```

## 8. Connect your platforms

This is the part only you can do — it's your logins and each platform's terms.
Register a developer app per platform (dev mode is enough for your own accounts),
and the **redirect URI to register** is:

```
https://majra-production.up.railway.app/connect/{platform}/callback
```

…with `{platform}` = `meta`, `google_ads`, `tiktok`, `snapchat`, or
`meta_social`. Put each platform's credentials in the **backend** service's
Variables (e.g. `MAJRA_META_APP_ID`, `MAJRA_META_APP_SECRET`), redeploy, then add
the connection from the dashboard. The full per-platform table is in
`docs/INSTALL.md` §7.

---

## If a service won't start

| Symptom in the logs | Cause | Fix |
|---|---|---|
| `Dockerfile does not exist` / build can't find it | Root Directory not set | Set it to `backend` or `frontend` (step 3). |
| `No module named psycopg2` or a driver error | (shouldn't happen — the normalizer handles it) | Confirm `MAJRA_DATABASE_URL` is the reference variable, not hand-typed. |
| backend: `MAJRA_ENCRYPTION_KEY … not set` | Missing on that service | It must be on backend, worker AND beat (step 4). |
| frontend loads but every action fails | `MAJRA_API_URL` wrong | Must be `http://backend.railway.internal:8000` exactly. |
| Connection fails: "redirect URI mismatch" | `MAJRA_PUBLIC_BASE_URL` ≠ what you registered | Make them identical — scheme, host, no trailing slash. |

Anything else: copy the failing service's Deploy Log and bring it to me. I can
read Railway logs and tell you what to change.

---

## What you're not getting on Railway (and when to care)

- **Prometheus/Grafana** — skipped. Railway has its own per-service metrics
  (CPU/memory/network) built in, which covers most of what you'd want. Add the
  monitoring services later if you want majra's pipeline dashboard specifically.
- **The nightly `pg_dump`** — replaced by Railway's managed-Postgres backups.
  Still worth reading `docs/OPERATIONS.md` on *testing* a restore.

None of that blocks you. Get it running, connect a platform, then decide what
else you want.
