import json
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
from app.connectors.meta import mapping
from app.core.config import settings
from app.models import RawMetaInsights

GRAPH_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"

# Graph error codes that are safe to retry (rate limits / transient).
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613}
AUTH_CODES = {102, 190}

INSIGHT_FIELDS = (
    "date_start,campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
    "spend,impressions,clicks,actions,action_values,video_play_actions"
)

BREAKDOWN_PARAMS = {
    "none": None,
    "country": "country",
    "device": "impression_device",
    "age_gender": "age,gender",
}


class MetaConnector(BaseConnector):
    platform = "meta"
    raw_model = RawMetaInsights

    # ------------------------------------------------------------- auth
    def auth_url(self, state: str, redirect_uri: str) -> str:
        q = urlencode(
            {
                "client_id": settings.meta_app_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": "ads_read",
                "response_type": "code",
            }
        )
        return f"{DIALOG}?{q}"

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        data = self._get(
            f"{GRAPH}/oauth/access_token",
            {
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        return self._to_long_lived(data["access_token"])

    def refresh(self, tokens: TokenSet) -> TokenSet:
        """Meta user tokens have no silent refresh; a still-valid long-lived
        token can be re-exchanged to extend it. If that fails, re-auth."""
        try:
            return self._to_long_lived(tokens.access_token)
        except (TransientError, PermanentError) as exc:
            raise PermanentError(
                "meta: token expired — re-authenticate the connection"
            ) from exc

    def _to_long_lived(self, short_token: str) -> TokenSet:
        data = self._get(
            f"{GRAPH}/oauth/access_token",
            {
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_token,
            },
        )
        expires = None
        if data.get("expires_in"):
            expires = (
                datetime.now(UTC) + timedelta(seconds=int(data["expires_in"]))
            ).isoformat()
        return TokenSet(access_token=data["access_token"], expires_at=expires)

    # -------------------------------------------------------- discovery
    def fetch_accounts(self, tokens: TokenSet) -> list[AccountInfo]:
        rows = self._paginate(
            f"{GRAPH}/me/adaccounts",
            {"fields": "name,currency,timezone_name", "limit": 100},
            tokens,
        )
        return [
            AccountInfo(
                external_id=r["id"],
                name=r.get("name") or r["id"],
                currency=r.get("currency"),
                timezone=r.get("timezone_name"),
            )
            for r in rows
        ]

    # ------------------------------------------------------------- data
    def fetch_entities(
        self, tokens: TokenSet, account_external_id: str, level: Level
    ) -> Iterator[dict]:
        path = {"campaign": "campaigns", "adset": "adsets", "ad": "ads"}[level]
        fields = {
            "campaign": "id,name,status,objective",
            "adset": "id,name,status,campaign_id",
            "ad": "id,name,status,adset_id,campaign_id",
        }[level]
        yield from self._paginate(
            f"{GRAPH}/{account_external_id}/{path}",
            {"fields": fields, "limit": 200},
            tokens,
        )

    def fetch_insights(
        self,
        tokens: TokenSet,
        account_external_id: str,
        date_from: date,
        date_to: date,
        breakdown: str,
    ) -> Iterator[dict]:
        params: dict = {
            "level": "ad",
            "time_increment": 1,
            "time_range": json.dumps(
                {"since": date_from.isoformat(), "until": date_to.isoformat()}
            ),
            "fields": INSIGHT_FIELDS,
            "limit": 500,
        }
        bd = BREAKDOWN_PARAMS.get(breakdown)
        if breakdown not in BREAKDOWN_PARAMS:
            raise PermanentError(f"meta: unsupported breakdown '{breakdown}'")
        if bd:
            params["breakdowns"] = bd
        yield from self._paginate(
            f"{GRAPH}/{account_external_id}/insights", params, tokens
        )

    # ---------------------------------------------------- normalization
    def normalize_entity(self, raw: dict, level: Level) -> dict:
        return {
            "external_id": raw["id"],
            "name": raw.get("name") or raw["id"],
            "status": raw.get("status"),
            "objective": raw.get("objective"),
            "campaign_external_id": raw.get("campaign_id"),
            "adset_external_id": raw.get("adset_id"),
        }

    def normalize_insight(self, raw: dict, breakdown: str) -> dict:
        purchases = mapping.sum_actions(raw.get("actions"), mapping.PURCHASE_TYPES)
        leads = mapping.sum_actions(raw.get("actions"), mapping.LEAD_TYPES)
        return {
            "date": raw["date_start"],
            "campaign_external_id": raw.get("campaign_id"),
            "campaign_name": raw.get("campaign_name"),
            "adset_external_id": raw.get("adset_id"),
            "adset_name": raw.get("adset_name"),
            "ad_external_id": raw.get("ad_id"),
            "ad_name": raw.get("ad_name"),
            "breakdown": breakdown,
            "country": raw.get("country"),
            "device": raw.get("impression_device"),
            "age": raw.get("age"),
            "gender": raw.get("gender"),
            "placement": None,
            "spend": float(raw.get("spend") or 0),
            "impressions": int(raw.get("impressions") or 0),
            "clicks": int(raw.get("clicks") or 0),
            "purchases": int(purchases),
            "leads": int(leads),
            "conversions": purchases + leads,
            "conversion_value": mapping.sum_actions(
                raw.get("action_values"), mapping.PURCHASE_TYPES
            ),
            "video_views": int(
                mapping.sum_actions(raw.get("video_play_actions"), mapping.VIDEO_VIEW_TYPES)
            ),
        }

    # ------------------------------------------------------------- http
    def _get(self, url: str, params: dict, tokens: TokenSet | None = None) -> dict:
        if tokens:
            params = {**params, "access_token": tokens.access_token}
        last = "meta: retries exhausted"
        for attempt in range(5):
            try:
                resp = httpx.get(url, params=params, timeout=60)
            except httpx.HTTPError as exc:  # network layer
                last = f"meta: network error {exc}"
                self._backoff(attempt)
                continue
            if resp.status_code >= 500:
                last = f"meta: server {resp.status_code}"
                self._backoff(attempt)
                continue
            data = resp.json()
            err = data.get("error")
            if not err:
                return data
            code = err.get("code")
            msg = f"meta: [{code}] {err.get('message')}"
            if code in AUTH_CODES or resp.status_code in (401, 403):
                raise PermanentError(msg)
            if code in TRANSIENT_CODES or err.get("is_transient"):
                last = msg
                self._backoff(attempt)
                continue
            raise PermanentError(msg)
        raise TransientError(last)

    def _paginate(self, url: str, params: dict, tokens: TokenSet) -> Iterator[dict]:
        data = self._get(url, params, tokens)
        while True:
            yield from data.get("data", [])
            nxt = (data.get("paging") or {}).get("next")
            if not nxt:
                return
            data = self._get(nxt, {}, tokens)

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 30) + random.random())
