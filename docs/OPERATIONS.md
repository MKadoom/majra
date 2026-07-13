# Operating majra

The runbook. Everything here assumes you're in `/opt/majra` on the server, and
that `C` is shorthand for:

```bash
alias C='docker compose -f infra/production/compose.prod.yml --env-file .env'
```

---

## Backups

The `backup` container dumps Postgres nightly at 03:30 UTC to `./backups`, keeping
14 days.

**This is not yet a backup.** A dump sitting on the same disk as the database
dies with the database. Copy it off the machine — for example, nightly to object
storage:

```bash
# on the server, after installing rclone and configuring a remote
cat >> /etc/cron.daily/majra-offsite <<'EOF'
#!/bin/sh
rclone copy /opt/majra/backups remote:majra-backups --max-age 48h
EOF
chmod +x /etc/cron.daily/majra-offsite
```

### Test the restore

An untested backup is a rumour. Do this once, now, so you know it works:

```bash
# 1. take a dump
C exec backup sh /backup.sh

# 2. restore it into a scratch database
LATEST=$(ls -1t backups/majra-*.sql.gz | head -1)
C exec -T postgres createdb -U majra majra_restore_test
gunzip -c "$LATEST" | C exec -T postgres psql -U majra -d majra_restore_test

# 3. check it actually has your data
C exec -T postgres psql -U majra -d majra_restore_test -c \
  "SELECT platform, count(*), max(date) FROM norm.fact_ad_performance GROUP BY 1;"

# 4. clean up
C exec -T postgres dropdb -U majra majra_restore_test
```

### Restore for real

```bash
C stop backend worker beat
gunzip -c backups/majra-2026-07-12T033000Z.sql.gz | \
  C exec -T postgres psql -U majra -d majra
C start backend worker beat
```

The dump uses `--clean --if-exists`, so it drops and recreates objects as it goes.

---

## Rotating secrets

### `MAJRA_SECRET_KEY` (JWT signing)

Safe to change at any time. The cost is that everyone is signed out and every
API key keeps working (keys aren't JWTs).

```bash
openssl rand -hex 32          # put it in .env
C up -d backend
```

### `MAJRA_ENCRYPTION_KEY` (platform tokens at rest)

**Careful.** This key decrypts every stored OAuth token. Change it and majra can
no longer read any of them — every connection must be re-authorized.

There is no rotation tool, deliberately: a half-finished re-encryption pass would
be worse than the problem. If you must rotate:

1. Generate the new key and put it in `.env`.
2. `C up -d backend worker beat`
3. Go to **Connections** and re-authorize each platform (and re-paste the
   service-account JSON for GA4 and Tag Manager).

Syncs will fail with a decryption error in between. That's expected.

### API keys

Revoke in **Settings** — takes effect immediately, on the next request. There's
no grace period.

---

## When a connection breaks

Platform tokens expire and get revoked. Meta's last roughly 60 days; Google
refreshes silently but dies if the user revokes access in their Google account.

**How you'll notice:** a gap in the channel strip on the Connections page, a
failed job in **Sync jobs**, and — if you set `MAJRA_ALERT_WEBHOOK_URL` — a POST
to your webhook.

**The fix** is always the same: **Connections → Add connection →** pick the same
platform and authorize again. The new tokens replace the old ones; historical
data is untouched.

Once the connection is healthy, backfill the days you lost:

```
Connections → Sync now
```

That pulls the last 30 days. For a longer gap, use the API:

```bash
curl -X POST https://majra.yourdomain.com/api/proxy/syncs \
  -H 'content-type: application/json' \
  -d '{"connection_id": 1, "date_from": "2026-05-01", "date_to": "2026-06-30"}'
```

Re-syncing a range you already have is safe — the write path deletes and
reinserts that exact scope, so you can't create duplicates.

---

## Upgrading

```bash
cd /opt/majra
git pull
C up -d --build
```

Migrations run automatically when the backend starts. Take a dump first
(`C exec backup sh /backup.sh`) if you like sleeping well.

---

## Reading the numbers when something looks wrong

**"The dashboard is missing yesterday."** Normal until the nightly sync runs.
Platforms don't finalize a day immediately, which is why majra fetches `yesterday`
and not `today`.

**"Yesterday's conversions changed overnight."** Also normal, and intentional.
Platforms restate conversions retroactively (an attribution window closes days
later), so every scheduled sync re-pulls the last **7 days** and rewrites them.
The number you saw was correct at the time; it isn't now.

**"Two platforms disagree about the same campaign."** They will. Each platform
attributes conversions by its own rules and window. majra reports what each
platform says — it does not try to reconcile them, because there is no honest way
to do that from the outside.

**"Spend doesn't add up across currencies."** majra keeps spend in each account's
own currency and carries the currency on every row. It does not convert. A blended
total across accounts in different currencies is meaningless, and majra would
rather show you the parts than invent an exchange rate.

---

## Scaling up

The default is one machine, and it goes a long way — Postgres handles tens of
millions of fact rows comfortably.

When one worker isn't keeping up:

```yaml
# compose.prod.yml
worker:
  command: celery -A app.workers.tasks.celery_app worker --concurrency=8
  deploy:
    replicas: 2
```

Note that with more than one API worker process, the rate limiter **must** be
Redis-backed (`MAJRA_RATE_LIMIT_STORAGE=redis://redis:6379/1`) — the production
compose already sets this. An in-memory limit with 4 workers lets 4× the traffic
through, which is the kind of bug you only find under attack.

---

## Health and monitoring

```bash
curl -s https://majra.yourdomain.com/health   # via Caddy? no — the API isn't public
C exec backend curl -s localhost:8000/health  # from inside
```

The API deliberately has **no published port**. The dashboard reaches it over the
internal Docker network; nothing else needs to.

Prometheus scrapes **`/_metrics`** — not `/metrics`, which is the ad-performance
query API. The metrics worth alerting on:

| Metric | Meaning |
|---|---|
| `majra_sync_total{status="failed"}` | Non-zero: a tributary is broken. |
| `majra_sync_rows_total` | Flat while syncs "succeed": a platform is returning nothing — check auth. |
| `majra_http_requests_total{status="5xx"}` | Something's actually wrong. |
| `majra_report_total{status="failed"}` | A saved report isn't being delivered. |
