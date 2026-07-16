"""Snapchat Ads connector (Marketing API v1).

Ad-shape connector (dataset="ad_performance"). Snapchat specifics: OAuth with a
refreshable access token; entities are wrapped one-per-type ({"campaign": {...}});
account-level stats come back as a nested timeseries (breakdown → ad → per-day
points) which is flattened here; spend and conversion value are micro-currency
(÷ 1e6); swipes map to clicks. The ad-level stats carry only the ad id, so
campaign/ad-squad are derived from the entity hierarchy by the engine.
"""
import random
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from app.connectors.base import (
    AccountInfo,
    BaseConnector,
    Level,
    PermanentError,
    TokenSet,
    TransientError,
)
from app.connectors.snapchat.config import SnapchatSettings
from app.models import RawSnapchatStats

API = "https://adsapi.snapchat.com/v1"
AUTH = "https://accounts.snapchat.com/login/oauth2/authorize"
TOKEN = "https://accounts.snapchat.com/login/oauth2/access_token"
SCOPE = "snapchat-marketing-api"

STAT_FIELDS = "spend,impressions,swipes,conversion_purchases,conversion_purchases_value"


class SnapchatConnector(BaseConnector):
    platform = "snapchat"
    raw_model = RawSnapchatStats

    def __init__(self) -> None:
        self._cfg = SnapchatSettings()
        self._access: str | None = None

    # ------------------------------------------------------------- auth
    def auth_url(self, state: str, redirect_uri: str) -> str:
        q = urlencode(
            {
                "response_type": "code",
                "client_id": self._cfg.snap_client_id,
                "redirect_uri": redirect_uri,
                "scope": SCOPE,
                "state": state,
            }
        )
        return f"{AUTH}?{q}"

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        data = self._token_endpoint(
            {
                "client_id": self._cfg.snap_client_id,
                "client_secret": self._cfg.snap_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
        )
        return TokenSet(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=self._expiry(data),
        )

    def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise PermanentError("snapchat: no refresh token — re-authenticate")
        data = self._token_endpoint(
            {
                "client_id": self._cfg.snap_client_id,
                "client_secret": self._cfg.snap_client_secret,
                "refresh_token": tokens.refresh_token,
                "grant_type": "refresh_token",
            }
        )
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token") or tokens.refresh_token,
            expires_at=self._expiry(data),
        )

    # -------------------------------------------------------- discovery
    def fetch_accounts(self, tokens: TokenSet) -> list[AccountInfo]:
        orgs = self._get(f"{API}/me/organizations", tokens).get("organizations", [])
        out: list[AccountInfo] = []
        for wrap in orgs:
            org = wrap.get("organization", {})
            org_id = org.get("id")
            if not org_id:
                continue
            accts = self._get(
                f"{API}/organizations/{org_id}/adaccounts", tokens
            ).get("adaccounts", [])
            for a in accts:
                acc = a.get("adaccount", {})
                out.append(
                    AccountInfo(
                        external_id=str(acc["id"]),
                        name=acc.get("name") or str(acc["id"]),
                        currency=acc.get("currency"),
                        timezone=acc.get("timezone"),
                    )
                )
        return out

    # ------------------------------------------------------------- data
    def fetch_entities(
        self, tokens: TokenSet, account_external_id: str, level: Level
    ) -> Iterator[dict]:
        path, key = {
            "campaign": ("campaigns", "campaign"),
            "adset": ("adsquads", "adsquad"),
            "ad": ("ads", "ad"),
        }[level]
        for item in self._paginate(
            f"{API}/adaccounts/{account_external_id}/{path}", tokens, key=path
        ):
            inner = item.get(key)
            if inner:
                yield inner

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
                f"snapchat: breakdown '{breakdown}' not supported (only 'none')"
            )
        # Snapchat requires DAY-granularity timeseries start/end to be midnight
        # in the ad account's own timezone (not UTC), or it 400s with
        # "must have a start time that is the start of the day".
        acct_resp = self._get(f"{API}/adaccounts/{account_external_id}", tokens)
        _accts = acct_resp.get("adaccounts", [])
        tz_name = (
            _accts[0].get("adaccount", {}).get("timezone") if _accts else None
        ) or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        # end_time is exclusive; add a day to include date_to.
        end_date = date_to + timedelta(days=1)
        start = datetime(
            date_from.year, date_from.month, date_from.day, tzinfo=tz
        ).isoformat()
        end = datetime(
            end_date.year, end_date.month, end_date.day, tzinfo=tz
        ).isoformat()
        data = self._get(
            f"{API}/adaccounts/{account_external_id}/stats",
            tokens,
            params={
                "granularity": "DAY",
                "breakdown": "ad",
                "fields": STAT_FIELDS,
                "start_time": start,
                "end_time": end,
            },
        )
        # Flatten timeseries_stats -> breakdown_stats.ad[] -> timeseries[].
        for ts in data.get("timeseries_stats", []):
            stat = ts.get("timeseries_stat", {})
            for ad in stat.get("breakdown_stats", {}).get("ad", []):
                ad_id = ad.get("id")
                for point in ad.get("timeseries", []):
                    yield {
                        "ad_id": ad_id,
                        "start_time": point.get("start_time"),
                        "stats": point.get("stats", {}),
                    }

    # ---------------------------------------------------- normalization
    def normalize_entity(self, raw: dict, level: Level) -> dict:
        if level == "campaign":
            return {
                "external_id": str(raw["id"]),
                "name": raw.get("name") or str(raw["id"]),
                "status": raw.get("status"),
                "objective": raw.get("objective"),
                "campaign_external_id": None,
                "adset_external_id": None,
            }
        if level == "adset":
            return {
                "external_id": str(raw["id"]),
                "name": raw.get("name") or str(raw["id"]),
                "status": raw.get("status"),
                "objective": None,
                "campaign_external_id": str(raw.get("campaign_id")),
                "adset_external_id": None,
            }
        return {
            "external_id": str(raw["id"]),
            "name": raw.get("name") or str(raw["id"]),
            "status": raw.get("status"),
            "objective": None,
            "campaign_external_id": None,
            "adset_external_id": str(raw.get("ad_squad_id")),
        }

    def normalize_insight(self, raw: dict, breakdown: str) -> dict:
        s = raw.get("stats", {})
        start = raw.get("start_time") or ""
        return {
            "date": start[:10],
            "campaign_external_id": None,   # derived from ad hierarchy by engine
            "adset_external_id": None,
            "ad_external_id": str(raw.get("ad_id")) if raw.get("ad_id") else None,
            "breakdown": breakdown,
            "country": None, "device": None, "age": None, "gender": None,
            "placement": None,
            "spend": float(s.get("spend") or 0) / 1_000_000,   # micro-currency
            "impressions": int(s.get("impressions") or 0),
            "clicks": int(s.get("swipes") or 0),               # swipes -> clicks
            "conversions": float(s.get("conversion_purchases") or 0),
            "conversion_value": float(s.get("conversion_purchases_value") or 0) / 1_000_000,
            "video_views": int(s.get("video_views") or 0),
            "leads": 0,
            "purchases": int(s.get("conversion_purchases") or 0),
        }

    # ------------------------------------------------------------- http
    def _token(self, tokens: TokenSet) -> str:
        if self._access is None:
            self._access = self.refresh(tokens).access_token if tokens.refresh_token \
                else tokens.access_token
        return self._access

    def _get(self, url: str, tokens: TokenSet, params: dict | None = None) -> dict:
        last = "snapchat: retries exhausted"
        for attempt in range(5):
            try:
                resp = httpx.get(
                    url,
                    headers={"Authorization": f"Bearer {self._token(tokens)}"},
                    params=params,
                    timeout=120,
                )
            except httpx.HTTPError as exc:
                last = f"snapchat: network error {exc}"
                self._backoff(attempt)
                continue
            if resp.status_code < 400:
                return resp.json()
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"snapchat: {resp.status_code}"
                self._backoff(attempt)
                continue
            raise PermanentError(f"snapchat: {resp.status_code} {resp.text[:200]}")
        raise TransientError(last)

    def _paginate(self, url: str, tokens: TokenSet, key: str) -> Iterator[dict]:
        params = {"limit": 200}
        while True:
            data = self._get(url, tokens, params=params)
            yield from data.get(key, [])
            nxt = (data.get("paging") or {}).get("next_link")
            if not nxt:
                return
            url, params = nxt, None  # next_link is a full URL

    def _token_endpoint(self, form: dict) -> dict:
        try:
            resp = httpx.post(TOKEN, data=form, timeout=60)
        except httpx.HTTPError as exc:
            raise TransientError(f"snapchat: oauth network error {exc}") from exc
        data = resp.json()
        if resp.status_code >= 400 or "access_token" not in data:
            detail = data.get("error_description") or data.get("error") or resp.status_code
            raise PermanentError(f"snapchat: oauth failed — {detail}")
        return data

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
