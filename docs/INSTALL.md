# Installing majra

From a bare Ubuntu 24.04 server to a working dashboard over HTTPS. Copy-paste
the commands; they're in order.

**You need:** a small VPS (2 vCPU / 4 GB RAM / 40 GB disk is comfortable), and a
domain name pointing at it.

You do **not** need any platform developer accounts yet — step 5 seeds demo data
so you can see the whole thing working first.

---

## 1. Point your domain at the server

Create an `A` record for `majra.yourdomain.com` → your server's IP. Do this
first: Caddy will try to get a TLS certificate as soon as it starts, and that
needs DNS to have propagated.

Check it from your laptop:

```bash
dig +short majra.yourdomain.com    # should print your server's IP
```

## 2. Install Docker

```bash
ssh root@your-server-ip

apt-get update && apt-get upgrade -y
curl -fsSL https://get.docker.com | sh
docker --version    # confirm it's there
```

## 3. Get majra onto the server

```bash
mkdir -p /opt && cd /opt
# either clone your repo, or upload the zip and unzip it:
#   git clone https://github.com/you/majra.git
cd majra
```

## 4. Configure

```bash
cp .env.example .env
```

Now generate the two secrets and put them in `.env`:

```bash
# MAJRA_SECRET_KEY
openssl rand -hex 32

# MAJRA_ENCRYPTION_KEY  (this one encrypts your platform tokens at rest —
# if you lose it, every connection has to be re-authorized)
docker run --rm python:3.12-slim sh -c \
  "pip install -q cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```

Edit `.env` and set, at minimum:

```ini
MAJRA_SECRET_KEY=<the openssl output>
MAJRA_ENCRYPTION_KEY=<the Fernet output>

MAJRA_PG_USER=majra
MAJRA_PG_PASSWORD=<a long random password>
MAJRA_PG_DB=majra

MAJRA_DOMAIN=majra.yourdomain.com
MAJRA_ACME_EMAIL=you@yourdomain.com

# This must be your real public URL — it's where the ad platforms redirect back
# to after you authorize them. Getting it wrong is the #1 cause of a failed
# connection.
MAJRA_PUBLIC_BASE_URL=https://majra.yourdomain.com
```

Lock the file down — it holds your database password and the key that decrypts
every platform token:

```bash
chmod 600 .env
```

## 5. Start it

```bash
docker compose -f infra/production/compose.prod.yml --env-file .env up -d --build
```

The first run builds images and takes a few minutes. Watch it come up:

```bash
docker compose -f infra/production/compose.prod.yml --env-file .env ps
docker compose -f infra/production/compose.prod.yml --env-file .env logs -f caddy
```

Caddy will fetch a certificate automatically. Once it's quiet, open
`https://majra.yourdomain.com` — you should get the sign-in page over HTTPS.

Migrations run automatically on backend start; there's nothing to run by hand.

## 6. See it working, before connecting anything

Load demo data so you can look at a full dashboard immediately:

```bash
docker compose -f infra/production/compose.prod.yml --env-file .env \
  exec backend python scripts/seed_demo.py --days 60
```

Sign in with **demo@majra.dev** / **demo-password**.

You'll see blended spend across four platforms, charts, exports — all of it. Note
that TikTok is missing two days on purpose: look at the channel strips on the
**Connections** page and you'll see the gap. That's what a broken connector looks
like, so you'll recognize it when it's real.

When you're done, wipe the demo data and create your own account:

```bash
docker compose -f infra/production/compose.prod.yml --env-file .env \
  exec backend python scripts/seed_demo.py --reset --days 0
```

## 7. Connect your real platforms

This is the part only you can do — it involves your logins and accepting each
platform's terms. Roughly 30–45 minutes, once.

Register a developer app on each platform you want, and put the credentials in
`.env`. **Development mode is enough** on every platform to pull data from your
*own* accounts — you only need full app review to connect *other people's*
accounts (clients).

| Platform | What to create | Where |
|---|---|---|
| Meta Ads | App (Business type) → Marketing API. Scope `ads_read` | developers.facebook.com |
| Google Ads | OAuth client (Web) + developer token from an MCC | console.cloud.google.com + Ads API Center |
| TikTok Ads | Developer app, Ads Management (read) + Reporting | business-api.tiktok.com |
| Snapchat Ads | OAuth app under your Business | business.snapchat.com |
| GA4 | A **service account** — no OAuth. Grant it Viewer on the property | console.cloud.google.com |
| Tag Manager | Same service account — grant it read on the container | console.cloud.google.com |
| Facebook/Instagram | The Meta app above, plus page/IG scopes | developers.facebook.com |

**The redirect URI**, for every OAuth platform, is:

```
https://majra.yourdomain.com/connect/{platform}/callback
```

…where `{platform}` is `meta`, `google_ads`, `tiktok`, `snapchat` or
`meta_social`. Register that exact URL in each developer console — platforms
match it strictly.

Then restart the backend to pick up the new credentials, and connect from the
dashboard:

```bash
docker compose -f infra/production/compose.prod.yml --env-file .env up -d backend
```

Go to **Connections → Add connection**. OAuth platforms open a consent screen;
GA4 and Tag Manager ask you to paste the service-account JSON key instead.

## 8. Set it running on its own

For each connection, choose **Sync daily** on the Connections page. That's it —
Celery beat handles the rest: a first backfill in month-sized chunks, then a
nightly incremental sync that also re-pulls the last 7 days (platforms restate
conversions after the fact, so yesterday's number is not final).

## Optional: monitoring

```bash
docker compose -f infra/production/compose.prod.yml --env-file .env \
  --profile monitoring up -d
```

Grafana binds to localhost only. Reach it through an SSH tunnel rather than
opening a port:

```bash
ssh -L 3001:localhost:3001 root@your-server-ip
# then open http://localhost:3001 (admin / MAJRA_GRAFANA_PASSWORD)
```

The **majra — pipeline health** dashboard is provisioned already.

## Optional: backups

The `backup` service already dumps the database nightly to `./backups` with
14-day rotation. **A backup on the same machine is not a backup** — see
`OPERATIONS.md` for copying it off the box and, more importantly, for testing a
restore.

---

## If something goes wrong

**Caddy can't get a certificate.** DNS hasn't propagated, or ports 80/443 are
blocked. Check `dig +short majra.yourdomain.com` and your firewall.

**A connection fails with "redirect URI mismatch".** `MAJRA_PUBLIC_BASE_URL` in
`.env` doesn't exactly match what you registered in the developer console. It has
to match exactly — scheme, host, no trailing slash.

**Backend won't start: "MAJRA_ENCRYPTION_KEY is not set".** Generate one (step 4).
It cannot be blank; token encryption depends on it.

**A sync fails with a 190 or "token expired".** The platform revoked the token —
Meta tokens expire roughly every 60 days. Reconnect that platform from the
Connections page.

**Everything's up but there's no data.** Check **Sync jobs** in the dashboard.
Every run is logged there step by step, with the error if it failed.
