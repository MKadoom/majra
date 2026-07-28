# Connecting TikTok Ads to majra

majra's TikTok connector is built and tested; connecting it is a one-time setup
of a TikTok for Business developer app plus a browser authorization. Everything
below happens on **your** TikTok account — majra only needs the resulting App ID,
App Secret, and one authorize click.

## 1. Create the developer app (Marketing API — the right portal)
> ⚠️ Use the **TikTok for Business / Marketing API** developer portal, **not**
> `developers.tiktok.com`. The latter only offers consumer products (Login Kit,
> Share Kit, Content Posting, Webhooks, Data Portability, Local Service) — there
> is **no Ads/Marketing product** there, so an app created on it cannot call the
> `business-api.tiktok.com` endpoints majra uses.

1. Go to <https://business-api.tiktok.com/portal> and register / sign in as a
   **TikTok for Business developer** (this is a different registration from the
   consumer developer portal). The advertiser account you want to report on must
   be reachable from this login — this is what the earlier email fix unblocked.
2. **Create a Marketing API app.** Give it a name and the required business
   details (category, description, Terms of Service URL, Privacy Policy URL).
3. From the app's page, copy its **App ID** and **App Secret**. Treat the Secret
   like a password — never commit it or paste it into a chat.

## 2. Register the redirect URI
In the app settings, add this exact **redirect URI** (must be absolute, begin
with `https`, under 512 chars — up to 10 are allowed):

    {MAJRA_PUBLIC_BASE_URL}/connect/tiktok/callback

For a Railway deployment that is, e.g.:

    https://your-majra.up.railway.app/connect/tiktok/callback

It must match `MAJRA_PUBLIC_BASE_URL` on your backend exactly, or TikTok rejects
the callback.

## 3. Request the reporting scope
Request the **advertiser read / Ads reporting** scope (read-only). majra only
reads campaign/ad-group/ad entities and the integrated report — it never writes.
For production-volume access TikTok requires passing their app review and
data-security check; test/sandbox access works before that for verifying the
flow.

## 4. Configure majra
Set these environment variables on the backend and restart:

    MAJRA_TIKTOK_APP_ID=<your App ID>
    MAJRA_TIKTOK_APP_SECRET=<your App Secret>

(They are already listed in `.env.example`.) If either is missing,
`GET /connect/tiktok` returns a clear `400 tiktok: not configured …` instead of
a broken consent screen.

## 5. Authorize
1. Signed in to majra, call `GET /connect/tiktok` (the Connections page does this
   for you). It returns an `auth_url`.
2. Open that URL, sign in to TikTok, and approve the requested access.
3. TikTok redirects to `/connect/tiktok/callback?auth_code=…&state=…`. majra
   exchanges the code, encrypts the token (Fernet), and creates the connection.
4. Trigger a sync (or wait for the schedule). Because the TikTok report is
   ad-level only, the engine derives each fact's campaign and ad-group from the
   entity hierarchy automatically — so `group_by=campaign` works.

## Notes
- TikTok access tokens are long-lived; the connector's `refresh()` is a no-op.
- API base: `https://business-api.tiktok.com/open_api/v1.3`. Responses are
  wrapped in `{code, message, data}`; auth uses an `Access-Token` header.
- Only the `none` breakdown is supported today; conversion **value** needs a
  specific TikTok value metric and is a v2 item (currently reported as 0).

## Sources
- [TikTok Marketing API v1.3: Endpoints, OAuth & Rate Limits (2026) — Soku](https://soku.ai/blog/tiktok-marketing-api-v1-3-explained)
- [How to build a TikTok Ads API integration — Rollout](https://rollout.com/integration-guides/tiktok-ads/how-to-build-a-public-tiktok-ads-integration-building-the-auth-flow)
- [How to Get Your TikTok Ads API Key — Unified.to](https://unified.to/blog/how_to_get_your_tiktok_ads_api_key)
