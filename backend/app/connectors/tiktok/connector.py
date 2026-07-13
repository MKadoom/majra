"""TikTok Ads connector (Business/Marketing API v1.3).

Ad-shape connector (dataset="ad_performance") — reuses the shared sync flow.
TikTok specifics: responses are wrapped in {code, message, data}; auth is an
`Access-Token` header (not Bearer); access tokens are long-lived (no refresh);
list-valued query params are JSON strings; the ad-level report carries only
`ad_id`, so campaign/adgroup are derived from the entity hierarchy by the engine.
"""
import json
import random
import time
from collections.abc import Iterator
from datetime import date
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
from app.connectors.tiktok.config import TikTokSettings
from app.models import RawTikTokReport

BASE = "https://business-api.tiktok.com/open_api/v1.3"
AUTH_PORTAL = "https://business-api.tiktok.com/portal/auth"

REPORT_METRICS = ["spend", "impressions", "clicks", "conversion", "video_play_actions"]


class TikTokConnector(BaseConnector):
    platform = "tiktok"
    raw_model = RawTikTokReport

    def __init__(self) -> None:
        self._cfg = TikTokSettings()

    # ------------------------------------------------------------- auth
    def auth_url(self, state: str, redirect_uri: str) -> str:
        q = urlencode(
            {"app_id": self._cfg.tiktok_app_id, "state": state, "redirect_uri": redirect_uri}
        )
        return f"{AUTH_PORTAL}?{q}"

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        # TikTok returns `auth_code` on the callback; the API layer passes it here
        # as `code`. The token exchange does not use redirect_uri.
        data = self._post(
            f"{BASE}/oauth2/access_token/",
            {
                "app_id": self._cfg.tiktok_app_id,
                "secret": self._cfg.tiktok_app_secret,
                "auth_code": code,
                "grant_type": "authorization_code",
            },
        )
        return TokenSet(access_token=data["access_token"])

    def refresh(self, tokens: TokenSet) -> TokenSet:
        # TikTok access tokens are long-lived; nothing to refresh.
        return tokens

    # -------------------------------------------------------- discovery
    def fetch_accounts(self, tokens: TokenSet) -> list[AccountInfo]:
        listing = self._get(
            f"{BASE}/oauth2/advertiser/get/",
            {"app_id": self._cfg.tiktok_app_id, "secret": self._cfg.tiktok_app_secret},
            tokens,
        )
        ids = [a["advertiser_id"] for a in listing.get("list", [])]
        if not ids:
            return []
        info = self._get(
            f"{BASE}/advertiser/info/",
            {"advertiser_ids": json.dumps(ids),
             "fields": json.dumps(["advertiser_id", "name", "currency", "timezone"])},
            tokens,
        )
        out = []
        for a in info.get("list", []):
            out.append(
                AccountInfo(
                    external_id=str(a["advertiser_id"]),
                    name=a.get("name") or str(a["advertiser_id"]),
                    currency=a.get("currency"),
                    timezone=a.get("timezone"),
                )
            )
        return out

    # ------------------------------------------------------------- data
    def fetch_entities(
        self, tokens: TokenSet, account_external_id: str, level: Level
    ) -> Iterator[dict]:
        endpoint = {"campaign": "campaign", "adset": "adgroup", "ad": "ad"}[level]
        yield from self._paginate(
            f"{BASE}/{endpoint}/get/", {"advertiser_id": account_external_id}, tokens
        )

    def fetch_insights(
        self,
        tokens: TokenSet,
        account_external_id: str,
        date_from: date,
        date_to: date,
        breakdown: str,
    ) -> Iterator[dict]:
        if breakdown != "none":
            raise PermanentError(
                f"tiktok: breakdown '{breakdown}' not supported (only 'none')"
            )
        params = {
            "advertiser_id": account_external_id,
            "report_type": "BASIC",
            "data_level": "AUCTION_AD",
            "dimensions": json.dumps(["ad_id", "stat_time_day"]),
            "metrics": json.dumps(REPORT_METRICS),
            "start_date": date_from.isoformat(),
            "end_date": date_to.isoformat(),
        }
        yield from self._paginate(f"{BASE}/report/integrated/get/", params, tokens)

    # ---------------------------------------------------- normalization
    def normalize_entity(self, raw: dict, level: Level) -> dict:
        if level == "campaign":
            return {
                "external_id": str(raw["campaign_id"]),
                "name": raw.get("campaign_name") or str(raw["campaign_id"]),
                "status": raw.get("operation_status"),
                "objective": raw.get("objective_type"),
                "campaign_external_id": None,
                "adset_external_id": None,
            }
        if level == "adset":
            return {
                "external_id": str(raw["adgroup_id"]),
                "name": raw.get("adgroup_name") or str(raw["adgroup_id"]),
                "status": raw.get("operation_status"),
                "objective": None,
                "campaign_external_id": str(raw.get("campaign_id")),
                "adset_external_id": None,
            }
        return {
            "external_id": str(raw["ad_id"]),
            "name": raw.get("ad_name") or str(raw["ad_id"]),
            "status": raw.get("operation_status"),
            "objective": None,
            "campaign_external_id": None,
            "adset_external_id": str(raw.get("adgroup_id")),
        }

    def normalize_insight(self, raw: dict, breakdown: str) -> dict:
        dims = raw.get("dimensions", {})
        m = raw.get("metrics", {})
        stat_day = dims.get("stat_time_day") or ""
        return {
            "date": stat_day[:10],  # "2026-06-01 00:00:00" -> "2026-06-01"
            "campaign_external_id": None,   # derived from ad hierarchy by engine
            "adset_external_id": None,
            "ad_external_id": str(dims.get("ad_id")) if dims.get("ad_id") else None,
            "breakdown": breakdown,
            "country": None, "device": None, "age": None, "gender": None,
            "placement": None,
            "spend": float(m.get("spend") or 0),   # account currency (not micros)
            "impressions": int(float(m.get("impressions") or 0)),
            "clicks": int(float(m.get("clicks") or 0)),
            "conversions": float(m.get("conversion") or 0),
            "conversion_value": 0.0,   # needs a specific TikTok value metric (v2)
            "video_views": int(float(m.get("video_play_actions") or 0)),
            "leads": 0,
            "purchases": 0,
        }

    # ------------------------------------------------------------- http
    def _headers(self, tokens: TokenSet) -> dict:
        return {"Access-Token": tokens.access_token, "Content-Type": "application/json"}

    def _check(self, resp: httpx.Response) -> dict:
        body = resp.json()
        code = body.get("code")
        if code in (0, None) and resp.status_code < 400:
            return body.get("data", {}) or {}
        msg = f"tiktok: [{code}] {body.get('message')}"
        # 40100/40105 auth; 50002 rate limit; 5xxxx server
        if code in (40100, 40105, 40001) or resp.status_code in (401, 403):
            raise PermanentError(msg)
        if code == 50002 or resp.status_code == 429 or resp.status_code >= 500:
            raise TransientError(msg)
        raise PermanentError(msg)

    def _get(self, url: str, params: dict, tokens: TokenSet) -> dict:
        return self._request("GET", url, tokens, params=params)

    def _post(self, url: str, json_body: dict) -> dict:
        # token exchange: no Access-Token header yet
        for attempt in range(5):
            try:
                resp = httpx.post(url, json=json_body, timeout=60)
            except httpx.HTTPError as exc:
                self._backoff(attempt)
                if attempt == 4:
                    raise TransientError(f"tiktok: network error {exc}") from exc
                continue
            return self._check(resp)
        raise TransientError("tiktok: retries exhausted")

    def _request(self, method: str, url: str, tokens: TokenSet, params=None) -> dict:
        last = "tiktok: retries exhausted"
        for attempt in range(5):
            try:
                resp = httpx.request(
                    method, url, headers=self._headers(tokens), params=params, timeout=120
                )
            except httpx.HTTPError as exc:
                last = f"tiktok: network error {exc}"
                self._backoff(attempt)
                continue
            try:
                return self._check(resp)
            except TransientError as exc:
                last = str(exc)
                self._backoff(attempt)
                continue
        raise TransientError(last)

    def _paginate(self, url: str, params: dict, tokens: TokenSet) -> Iterator[dict]:
        page = 1
        while True:
            data = self._request(
                "GET", url, tokens, params={**params, "page": page, "page_size": 1000}
            )
            yield from data.get("list", [])
            info = data.get("page_info", {})
            total_page = int(info.get("total_page", 1) or 1)
            if page >= total_page:
                return
            page += 1

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 30) + random.random())
