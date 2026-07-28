# majra — Looker Studio connector

One data source for every channel. Because majra already normalises Meta,
Google Ads, TikTok and Snapchat into a single shape, you don't add four
connectors and blend them in the report — you add this once and group by
platform.

Three datasets, one per data source you create:

| Dataset | Endpoint | Fields |
|---|---|---|
| Paid ads | `/metrics` | date, platform, account, campaign, adset, ad, country, device · spend, impressions, clicks, conversions, conversion value, video views, leads, purchases · CTR, CPC, CPM, CPA, ROAS |
| Web analytics | `/web-metrics` | date, source, medium, campaign, account · sessions, users, conversions, revenue |
| Organic social | `/social-metrics` | date, grain, reference, account · impressions, reach, engagement, followers |

## Deploy it

Community connectors run as Apps Script under **your** Google account. Nothing
is published to Google's gallery, and your data never passes through a third
party — Looker Studio calls your majra directly.

1. Go to [script.google.com](https://script.google.com) → **New project**.
2. Paste `Code.gs` over the default `Code.gs`.
3. **Project Settings** → tick *Show `appsscript.json` manifest file*, then open
   it in the editor and paste this folder's `appsscript.json` over it.
4. **Deploy** → *Test deployments* → select **Looker Studio** → copy the link,
   or **Deploy** → *New deployment* for a stable one.
5. Open the deployment link. Looker Studio asks to authorise the script, then
   asks for credentials.

## Credentials

| Field | Value |
|---|---|
| **Username** | your majra URL, e.g. `https://your-majra.up.railway.app` |
| **Password / token** | a majra API key (`mk_…`) from **Settings → API keys** |

Both are checked at connect time against `/v1/accounts`, so a wrong URL or an
expired key fails immediately rather than producing an empty chart later.

## Notes

- **Read-only.** The key only reaches endpoints that read. A report can never
  trigger a sync or touch an ad platform.
- **More metrics than the dashboard shows.** majra's API has always computed
  `video_views`, `leads`, `purchases`, `CPM` and `CPA`; the built-in Explore
  page just doesn't display them. They're all exposed here.
- **CTR, CPC, CPM, CPA and ROAS are not summed.** They're ratios computed per
  row by majra, so they're marked `NO_AGGREGATION` — the average of per-row
  CPCs is not the overall CPC. For an aggregated figure, build a calculated
  field from spend / clicks / value in the report.
- **Grouping follows the chart.** The connector asks majra to group by exactly
  the dimensions your chart uses, so a scorecard fetches one row and a campaign
  table fetches many.
- **Dates** are passed through from the report's date range; majra returns
  complete days.

## Troubleshooting

**"majra returned 401"** — the API key was revoked or regenerated. Edit the data
source credentials and paste a current key.

**"majra returned 404"** — the URL is missing the `/v1` passthrough. Make sure
your frontend is on a build that includes the `/v1/:path*` rewrite in
`frontend/next.config.mjs`.

**Empty charts, no error** — usually a date range with no synced data. Check
**Sync jobs** in majra for a successful run covering those dates.
