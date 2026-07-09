"""Facebook Pages + Instagram organic-social connector.

dataset="social_performance" → fact_social_performance. Uses the same Meta app
as the ads connector (settings.meta_app_id/secret) but a separate connection
authorized with page/IG scopes. Discovery (/me/accounts) yields the Pages and
caches each page's access token + connected IG account; page and IG user
insights are day-period series that this connector pivots into flat per-day rows
(grain "page" / "ig_user"). Post-level insights are a straightforward extension.

Graph day-period insights report `end_time` as the start of the day *after* the
one measured, so the row date is end_time − 1 day.
"""
import random
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterator
from urllib.parse import urlencode

import httpx

from app.connectors.base import (
    AccountInfo, BaseConnector, Level, PermanentError, TokenSet, TransientError,
)
from app.core.config import settings
from app.models import RawMetaSocial

GRAPH_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
SCOPES = "pages_show_list,pages_read_engagement,read_insights,instagram_basic,instagram_manage_insights"

AUTH_CODES = {102, 190}
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613}

PAGE_METRICS = {
    "page_impressions": "impressions",
    "page_impressions_unique": "reach",
    "page_post_engagements": "engagement",
    "page_fans": "followers",
}
IG_METRICS = {
    "impressions": "impressions",
    "reach": "reach",
    "follower_count": "followers",
}


class MetaSocialConnector(BaseConnector):
    platform = "meta_social"
    raw_model = RawMetaSocial
    dataset = "social_performance"

    def __init__(self) -> None:
        self._page_tokens: dict[str, str] = {}
        self._ig_accounts: dict[str, str] = {}  # page_id -> ig_user_id

    # ------------------------------------------------------------- auth
    def auth_url(self, state: str, redirect_uri: str) -> str:
        q = urlencode(
            {
                "client_id": settings.meta_app_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": SCOPES,
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
        try:
            return self._to_long_lived(tokens.access_token)
        except (TransientError, PermanentError):
            raise PermanentError("meta_social: token expired — re-authenticate")

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
                datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
            ).isoformat()
        return TokenSet(access_token=data["access_token"], expires_at=expires)

    # -------------------------------------------------------- discovery
    def fetch_accounts(self, tokens: TokenSet) -> list[AccountInfo]:
        pages = self._paginate(
            f"{GRAPH}/me/accounts",
            {"fields": "name,access_token,instagram_business_account", "limit": 100},
            tokens,
        )
        out: list[AccountInfo] = []
        for p in pages:
            pid = str(p["id"])
            self._page_tokens[pid] = p.get("access_token", tokens.access_token)
            ig = p.get("instagram_business_account") or {}
            if ig.get("id"):
                self._ig_accounts[pid] = str(ig["id"])
            out.append(AccountInfo(external_id=pid, name=p.get("name") or pid))
        return out

    def fetch_entities(
        self, tokens: TokenSet, account_external_id: str, level: Level
    ) -> Iterator[dict]:
        return iter(())  # organic social has no ad hierarchy

    # ------------------------------------------------------------- data
    def fetch_insights(
        self,
        tokens: TokenSet,
        account_external_id: str,
        date_from: date,
        date_to: date,
        breakdown: str,
    ) -> Iterator[dict]:
        page_id = account_external_id
        page_token = TokenSet(access_token=self._page_tokens.get(page_id, tokens.access_token))
        # Graph day-period `until` is exclusive of the last day's row; pad by 2.
        since = int(datetime(date_from.year, date_from.month, date_from.day,
                             tzinfo=timezone.utc).timestamp())
        until = int((datetime(date_to.year, date_to.month, date_to.day,
                              tzinfo=timezone.utc) + timedelta(days=2)).timestamp())

        # Page-level insights
        page_data = self._get(
            f"{GRAPH}/{page_id}/insights",
            {"metric": ",".join(PAGE_METRICS), "period": "day",
             "since": since, "until": until},
            page_token,
        ).get("data", [])
        yield from self._pivot(page_data, PAGE_METRICS, "page", page_id, date_from, date_to)

        # Instagram user insights (if a business account is linked)
        ig_id = self._ig_accounts.get(page_id)
        if ig_id:
            ig_data = self._get(
                f"{GRAPH}/{ig_id}/insights",
                {"metric": ",".join(IG_METRICS), "period": "day",
                 "since": since, "until": until},
                page_token,
            ).get("data", [])
            yield from self._pivot(ig_data, IG_METRICS, "ig_user", ig_id, date_from, date_to)

    @staticmethod
    def _pivot(series_list, metric_map, grain, ref, date_from, date_to):
        """Graph insights return one series per metric; pivot to per-day rows."""
        by_date: dict[str, dict] = {}
        for series in series_list:
            key = metric_map.get(series.get("name"))
            if not key:
                continue
            for v in series.get("values", []):
                end = (v.get("end_time") or "")[:10]
                if len(end) != 10:
                    continue
                d = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
                if d < date_from.isoformat() or d > date_to.isoformat():
                    continue
                by_date.setdefault(d, {})[key] = int(v.get("value") or 0)
        for d, metrics in sorted(by_date.items()):
            yield {"grain": grain, "external_ref": ref, "date": d, **metrics}

    # ---------------------------------------------------- normalization
    def normalize_entity(self, raw: dict, level: Level) -> dict:
        return {}  # unused

    def normalize_insight(self, raw: dict, breakdown: str) -> dict:
        return {
            "date": raw["date"],
            "grain": raw.get("grain", "page"),
            "external_ref": raw.get("external_ref"),
            "impressions": int(raw.get("impressions") or 0),
            "reach": int(raw.get("reach") or 0),
            "engagement": int(raw.get("engagement") or 0),
            "followers": int(raw.get("followers") or 0),
        }

    # ------------------------------------------------------------- http
    def _get(self, url: str, params: dict, tokens: TokenSet | None = None) -> dict:
        if tokens:
            params = {**params, "access_token": tokens.access_token}
        last = "meta_social: retries exhausted"
        for attempt in range(5):
            try:
                resp = httpx.get(url, params=params, timeout=90)
            except httpx.HTTPError as exc:
                last = f"meta_social: network error {exc}"
                self._backoff(attempt)
                continue
            if resp.status_code >= 500:
                last = f"meta_social: server {resp.status_code}"
                self._backoff(attempt)
                continue
            data = resp.json()
            err = data.get("error")
            if not err:
                return data
            code = err.get("code")
            msg = f"meta_social: [{code}] {err.get('message')}"
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
