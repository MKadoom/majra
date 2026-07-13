"""Google Ads connector.

Uses the Google Ads REST API (googleAds:searchStream + GAQL) over httpx — the
same lightweight pattern as the Meta connector, avoiding the heavy google-ads
SDK and its protobuf/config machinery. Slots into the SDK with no changes to
app/core or app/api.

Google Ads deprecates API versions roughly yearly; bump API_VERSION when needed.
"""
import random
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.connectors.base import (
    AccountInfo,
    BaseConnector,
    Level,
    PermanentError,
    TokenSet,
    TransientError,
)
from app.connectors.google_ads.config import GoogleAdsSettings
from app.models import RawGoogleInsights

API_VERSION = "v18"
API = f"https://googleads.googleapis.com/{API_VERSION}"
OAUTH_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/adwords"

# device / country segments map onto the shared fact columns (SPEC §3).
BREAKDOWN_SEGMENTS = {
    "none": None,
    "device": "segments.device",
    "country": "segments.geo_target_country",
}

INSIGHT_METRICS = [
    "metrics.cost_micros",
    "metrics.impressions",
    "metrics.clicks",
    "metrics.conversions",
    "metrics.conversions_value",
    "metrics.video_views",
]
INSIGHT_DIMS = [
    "segments.date",
    "campaign.id",
    "campaign.name",
    "ad_group.id",
    "ad_group.name",
    "ad_group_ad.ad.id",
    "ad_group_ad.ad.name",
]


class GoogleAdsConnector(BaseConnector):
    platform = "google_ads"
    raw_model = RawGoogleInsights

    def __init__(self) -> None:
        self._cfg = GoogleAdsSettings()
        self._access: str | None = None  # cached access token for this sync

    # ------------------------------------------------------------- auth
    def auth_url(self, state: str, redirect_uri: str) -> str:
        q = urlencode(
            {
                "client_id": self._cfg.google_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",     # get a refresh token
                "prompt": "consent",          # ensure refresh token on re-consent
                "include_granted_scopes": "true",
                "state": state,
            }
        )
        return f"{OAUTH_AUTH}?{q}"

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        data = self._token_endpoint(
            {
                "code": code,
                "client_id": self._cfg.google_client_id,
                "client_secret": self._cfg.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        return TokenSet(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=self._expiry(data),
        )

    def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise PermanentError("google_ads: no refresh token — re-authenticate")
        data = self._token_endpoint(
            {
                "refresh_token": tokens.refresh_token,
                "client_id": self._cfg.google_client_id,
                "client_secret": self._cfg.google_client_secret,
                "grant_type": "refresh_token",
            }
        )
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=tokens.refresh_token,
            expires_at=self._expiry(data),
        )

    # -------------------------------------------------------- discovery
    def fetch_accounts(self, tokens: TokenSet) -> list[AccountInfo]:
        resp = self._request(
            "GET", f"{API}/customers:listAccessibleCustomers", tokens
        )
        resource_names = resp.json().get("resourceNames", [])
        out: list[AccountInfo] = []
        query = (
            "SELECT customer.id, customer.descriptive_name, "
            "customer.currency_code, customer.time_zone FROM customer"
        )
        for rn in resource_names:
            cid = rn.split("/")[-1]  # "customers/123" -> "123"
            rows = list(self._search(tokens, cid, query))
            if not rows:
                out.append(AccountInfo(external_id=cid, name=cid))
                continue
            c = rows[0].get("customer", {})
            out.append(
                AccountInfo(
                    external_id=str(c.get("id", cid)),
                    name=c.get("descriptiveName") or cid,
                    currency=c.get("currencyCode"),
                    timezone=c.get("timeZone"),
                )
            )
        return out

    # ------------------------------------------------------------- data
    def fetch_entities(
        self, tokens: TokenSet, account_external_id: str, level: Level
    ) -> Iterator[dict]:
        queries = {
            "campaign": (
                "SELECT campaign.id, campaign.name, campaign.status, "
                "campaign.advertising_channel_type FROM campaign"
            ),
            "adset": (
                "SELECT ad_group.id, ad_group.name, ad_group.status, "
                "campaign.id FROM ad_group"
            ),
            "ad": (
                "SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, "
                "ad_group_ad.status, ad_group.id FROM ad_group_ad"
            ),
        }
        yield from self._search(tokens, account_external_id, queries[level])

    def fetch_insights(
        self,
        tokens: TokenSet,
        account_external_id: str,
        date_from: date,
        date_to: date,
        breakdown: str,
    ) -> Iterator[dict]:
        if breakdown not in BREAKDOWN_SEGMENTS:
            raise PermanentError(
                f"google_ads: unsupported breakdown '{breakdown}' "
                "(supported: none, device, country)"
            )
        fields = INSIGHT_DIMS + INSIGHT_METRICS
        seg = BREAKDOWN_SEGMENTS[breakdown]
        if seg:
            fields = fields + [seg]
        query = (
            f"SELECT {', '.join(fields)} FROM ad_group_ad "
            f"WHERE segments.date BETWEEN '{date_from.isoformat()}' "
            f"AND '{date_to.isoformat()}'"
        )
        yield from self._search(tokens, account_external_id, query)

    # ---------------------------------------------------- normalization
    def normalize_entity(self, raw: dict, level: Level) -> dict:
        if level == "campaign":
            c = raw.get("campaign", {})
            return {
                "external_id": str(c.get("id")),
                "name": c.get("name") or str(c.get("id")),
                "status": c.get("status"),
                "objective": c.get("advertisingChannelType"),
                "campaign_external_id": None,
                "adset_external_id": None,
            }
        if level == "adset":
            g = raw.get("adGroup", {})
            return {
                "external_id": str(g.get("id")),
                "name": g.get("name") or str(g.get("id")),
                "status": g.get("status"),
                "objective": None,
                "campaign_external_id": str(raw.get("campaign", {}).get("id")),
                "adset_external_id": None,
            }
        # ad
        ada = raw.get("adGroupAd", {})
        ad = ada.get("ad", {})
        return {
            "external_id": str(ad.get("id")),
            "name": ad.get("name") or str(ad.get("id")),
            "status": ada.get("status"),
            "objective": None,
            "campaign_external_id": None,
            "adset_external_id": str(raw.get("adGroup", {}).get("id")),
        }

    def normalize_insight(self, raw: dict, breakdown: str) -> dict:
        m = raw.get("metrics", {})
        seg = raw.get("segments", {})
        camp = raw.get("campaign", {})
        ag = raw.get("adGroup", {})
        ad = raw.get("adGroupAd", {}).get("ad", {})
        cost_micros = float(m.get("costMicros") or 0)
        device = (seg.get("device") or "").lower() or None
        # geo_target_country is a geoTargetConstant resource name
        # (e.g. "geoTargetConstants/2158"); ISO mapping is a v2 lookup.
        country = seg.get("geoTargetCountry")
        return {
            "date": seg.get("date"),
            "campaign_external_id": str(camp["id"]) if camp.get("id") else None,
            "campaign_name": camp.get("name"),
            "adset_external_id": str(ag["id"]) if ag.get("id") else None,
            "adset_name": ag.get("name"),
            "ad_external_id": str(ad["id"]) if ad.get("id") else None,
            "ad_name": ad.get("name"),
            "breakdown": breakdown,
            "country": country,
            "device": device,
            "age": None,
            "gender": None,
            "placement": None,
            "spend": cost_micros / 1_000_000,
            "impressions": int(m.get("impressions") or 0),
            "clicks": int(m.get("clicks") or 0),
            # Google's default report doesn't split purchase vs lead without
            # conversion-action segmentation; total conversions only for now.
            "purchases": 0,
            "leads": 0,
            "conversions": float(m.get("conversions") or 0),
            "conversion_value": float(m.get("conversionsValue") or 0),
            "video_views": int(m.get("videoViews") or 0),
        }

    # ------------------------------------------------------------- http
    def _token(self, tokens: TokenSet) -> str:
        """Return a valid access token, refreshing from the refresh token once
        per sync (stored expiry is usually stale, so we just refresh)."""
        if self._access is None:
            if tokens.refresh_token:
                self._access = self.refresh(tokens).access_token
            else:
                self._access = tokens.access_token
        return self._access

    def _headers(self, tokens: TokenSet, with_login_customer: bool) -> dict:
        h = {
            "Authorization": f"Bearer {self._token(tokens)}",
            "developer-token": self._cfg.google_ads_developer_token,
            "Content-Type": "application/json",
        }
        if with_login_customer and self._cfg.google_ads_login_customer_id:
            h["login-customer-id"] = self._cfg.google_ads_login_customer_id
        return h

    def _search(
        self, tokens: TokenSet, customer_id: str, query: str
    ) -> Iterator[dict]:
        url = f"{API}/customers/{customer_id}/googleAds:searchStream"
        resp = self._request("POST", url, tokens, json={"query": query})
        # searchStream returns a JSON array of {"results": [...]} batches.
        payload = resp.json()
        if isinstance(payload, dict):  # single batch (defensive)
            payload = [payload]
        for batch in payload:
            yield from batch.get("results", [])

    def _token_endpoint(self, form: dict) -> dict:
        try:
            resp = httpx.post(OAUTH_TOKEN, data=form, timeout=60)
        except httpx.HTTPError as exc:
            raise TransientError(f"google_ads: oauth network error {exc}") from exc
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            detail = data.get("error_description") or data.get("error") or resp.status_code
            raise PermanentError(f"google_ads: oauth failed — {detail}")
        return data

    def _request(
        self, method: str, url: str, tokens: TokenSet, json: dict | None = None
    ) -> httpx.Response:
        # listAccessibleCustomers must NOT carry login-customer-id.
        with_login = not url.endswith("listAccessibleCustomers")
        last = "google_ads: retries exhausted"
        for attempt in range(5):
            try:
                resp = httpx.request(
                    method,
                    url,
                    headers=self._headers(tokens, with_login),
                    json=json,
                    timeout=120,
                )
            except httpx.HTTPError as exc:
                last = f"google_ads: network error {exc}"
                self._backoff(attempt)
                continue
            if resp.status_code < 400:
                return resp
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"google_ads: {resp.status_code}"
                self._backoff(attempt)
                continue
            # 4xx (auth revoked, bad query, dev-token/permission) -> permanent
            raise PermanentError(f"google_ads: {resp.status_code} {self._err(resp)}")
        raise TransientError(last)

    @staticmethod
    def _err(resp: httpx.Response) -> str:
        try:
            return str(resp.json().get("error", {}).get("message", ""))[:300]
        except Exception:
            return resp.text[:300]

    @staticmethod
    def _expiry(data: dict) -> str | None:
        if not data.get("expires_in"):
            return None
        return (
            datetime.now(UTC) + timedelta(seconds=int(data["expires_in"]))
        ).isoformat()

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 30) + random.random())
