# Adding a connector

A connector is a self-contained folder. Seven of them exist, and none needed a
change to `app/core` — if you find yourself editing core, stop and ask why.

Read `docs/SPEC.md` §2 first; it's the binding contract.

---

## The shape of one

```
app/connectors/<slug>/
    __init__.py
    config.py       # optional: connector-scoped settings (MAJRA_ prefix)
    connector.py    # the class
```

Then one line in `app/connectors/__init__.py` to import it — that's what
registers it (`BaseConnector.__init_subclass__` does the rest).

```python
class AcmeConnector(BaseConnector):
    platform = "acme"                 # the slug used everywhere
    raw_model = RawAcmeReport         # append-only raw table (or None)
    dataset = "ad_performance"        # which fact shape it feeds
    auth_kind = "oauth"               # or "service_account"
```

### `dataset` — pick the shape it feeds

| dataset | Fact table | Used by |
|---|---|---|
| `ad_performance` | `norm.fact_ad_performance` | Meta, Google, TikTok, Snapchat |
| `web_performance` | `norm.fact_web_performance` | GA4 |
| `social_performance` | `norm.fact_social_performance` | Facebook Pages / Instagram |
| `config_snapshot` | `norm.gtm_resources` | Tag Manager |

**Reusing an existing shape means you touch no engine code at all.** A new shape
means adding one handler to `DATASET_HANDLERS` in `app/services/sync.py` — do
that only if the data genuinely doesn't fit, as GA4's didn't.

### The methods

```python
# auth
auth_url(state, redirect_uri) -> str
exchange_code(code, redirect_uri) -> TokenSet
refresh(tokens) -> TokenSet
# or, for auth_kind="service_account":
credentials_from_input(payload) -> TokenSet

# discovery
fetch_accounts(tokens) -> list[AccountInfo]

# data — iterators; handle the platform's pagination inside
fetch_entities(tokens, account_external_id, level) -> Iterator[dict]
fetch_insights(tokens, account_external_id, date_from, date_to, breakdown) -> Iterator[dict]
# …or, for dataset="config_snapshot":
snapshot(tokens, account_external_id) -> Iterator[dict]

# normalization — PURE functions. This is what you unit-test.
normalize_entity(raw, level) -> dict
normalize_insight(raw, breakdown) -> dict
```

### Errors: only two

```python
raise TransientError(...)   # rate limit, 5xx, timeout  → Celery retries it
raise PermanentError(...)   # revoked auth, bad request → the job fails now
```

Nothing else may escape a connector. Everything upstream depends on this.

---

## What to watch out for

These are the things that actually bit us, in the order they'll bite you:

**Dates.** Platforms lie about days in creative ways. Meta's Graph day-period
insights stamp a value with the *end* of the day, so the row for June 1st comes
back labelled June 2nd — `meta_social` subtracts a day. TikTok returns
`"2026-06-01 00:00:00"` and needs slicing. GA4 returns `20260601`. Write a unit
test with a real captured row before you trust anything.

**Money.** Google reports `cost_micros`; Snapchat reports micro-currency for both
spend *and* conversion value. Divide by 1e6, and say so in a comment, or someone
will "fix" it later.

**Ad-level-only reporting.** TikTok and Snapchat give you `ad_id` and nothing
above it. Don't paper over this in the connector — return `None` for the
campaign/adset ids and let the engine derive them from the entity hierarchy,
which it already does.

**Breakdowns.** Rows from different breakdown sets must never be summed together
(SPEC §3) — the country breakdown of a day sums to the same spend as its `none`
row. If your platform can't do a breakdown, raise `PermanentError` rather than
silently returning `none` data.

**Currency.** Keep it in the account's currency, carry the code on every row, and
don't convert.

---

## Testing it

Every connector ships fixture-based normalization tests plus one end-to-end sync
against a **mocked API** (`respx`). Capture a real response page, redact the ids,
and use it as the fixture. See `tests/test_google_ads.py` — it's the cleanest
example, and it also asserts the blended query works across two platforms.

The one test worth copying verbatim is the idempotency check:

```python
r1 = auth_client.post("/syncs", json=body)      # inserts N rows
r2 = auth_client.post("/syncs", json=body)      # deletes N, inserts N
assert r2.json()["stats"]["rows_deleted"] == N
assert count(FactAdPerformance) == N            # not 2N
```

If that passes, your connector can be re-run safely — which is the whole point.

---

## Checklist

- [ ] `app/connectors/<slug>/connector.py` with the class
- [ ] Registered in `app/connectors/__init__.py`
- [ ] Raw model in `app/models/norm_models.py`, exported in `app/models/__init__.py`
- [ ] Alembic migration for the raw table (copy `0004`, it's the simplest)
- [ ] Env vars in `.env.example`
- [ ] Fixture tests for `normalize_*`
- [ ] One end-to-end mocked sync test, including the idempotency assertion
- [ ] Platform colour + label in `frontend/src/lib/api.ts` so it appears in the UI
- [ ] `git diff --stat -- backend/app/core` shows **nothing**
