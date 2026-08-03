"""WhatsApp Business Management connector — marketing-message analytics.

Not an ad platform: this reads the WhatsApp Business Management API's
*template analytics* (each message template ≈ a marketing "campaign") and maps
the messaging metrics onto the ad_performance fact shape so they flow through the
same /metrics + reports:

    delivered  -> impressions   (the headline "reach" of a broadcast)
    clicked    -> clicks
    read       -> conversions
    sent       -> (kept in raw only)
    spend       = 0             (marketing messages are priced separately)

Auth is a single System User token (whatsapp_business_management) read from env
(MAJRA_WHATSAPP_TOKEN); a connection stores which WABAs to pull in tokens.extra.
`insights_carry_hierarchy` is True — each row carries its template as the
campaign, so the engine skips the entity walk (there are no ad-sets/ads here).
"""
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime

import httpx

from app.connectors.base import (
    AccountInfo,
    BaseConnector,
    Level,
    PermanentError,
    TokenSet,
    TransientError,
)
from app.connectors.whatsapp.config import WhatsAppSettings

METRIC_TYPES = ["SENT", "DELIVERED", "READ", "CLICKED"]
TEMPLATES_PER_CALL = 10  # template_analytics caps template_ids at 10 per request


class WhatsAppConnector(BaseConnector):
    platform = "whatsapp"
    raw_model = None                       # no raw table → no migration needed
    dataset = "ad_performance"
    auth_kind = "service_account"
    insights_carry_hierarchy = True        # template == campaign; no entity walk

    def __init__(self) -> None:
        self._cfg = WhatsAppSettings()
        self._base = f"https://graph.facebook.com/{self._cfg.whatsapp_api_version}"

    def _require_config(self) -> None:
        if not self._cfg.whatsapp_token:
            raise PermanentError(
                "whatsapp: not configured — set MAJRA_WHATSAPP_TOKEN (a System User "
                "token with whatsapp_business_management) and restart the service"
            )

    # ------------------------------------------------- connection setup
    def credentials_from_input(self, payload: dict) -> TokenSet:
        """A WhatsApp connection just pins which WABAs to pull. The token lives in
        env, so nothing secret is stored in the connection."""
        wabas = payload.get("waba_ids") or payload.get("account_ids")
        if isinstance(wabas, str):
            wabas = [w.strip() for w in wabas.split(",") if w.strip()]
        if not wabas:
            raise PermanentError("whatsapp: waba_ids (WhatsApp Business Account IDs) required")
        return TokenSet(access_token="", extra={"waba_ids": [str(w) for w in wabas]})

    # OAuth methods are not applicable.
    def auth_url(self, state: str, redirect_uri: str) -> str:
        raise PermanentError("whatsapp uses a token; connect via /connections/service-account")

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        raise PermanentError("whatsapp uses a token; connect via /connections/service-account")

    def refresh(self, tokens: TokenSet) -> TokenSet:
        return tokens  # long-lived System User token; nothing to refresh

    # -------------------------------------------------------- discovery
    def fetch_accounts(self, tokens: TokenSet) -> list[AccountInfo]:
        self._require_config()
        out: list[AccountInfo] = []
        for waba in tokens.extra.get("waba_ids", []):
            info = self._get(f"{self._base}/{waba}", {"fields": "id,name,currency"})
            out.append(
                AccountInfo(
                    external_id=str(waba),
                    name=info.get("name") or f"WABA {waba}",
                    currency=info.get("currency"),
                )
            )
        return out

    def fetch_entities(
        self, tokens: TokenSet, account_external_id: str, level: Level
    ) -> Iterator[dict]:
        return iter(())  # templates come through as campaigns on the insight rows

    # ------------------------------------------------------------- data
    def _templates(self, waba: str) -> dict[str, str]:
        """external_id -> name for every message template on the WABA."""
        names: dict[str, str] = {}
        url = f"{self._base}/{waba}/message_templates"
        params = {"fields": "id,name", "limit": 200}
        while True:
            data = self._get(url, params)
            for t in data.get("data", []):
                if t.get("id"):
                    names[str(t["id"])] = t.get("name") or str(t["id"])
            nxt = (data.get("paging") or {}).get("next")
            if not nxt:
                return names
            url, params = nxt, None  # `next` is a fully-formed URL

    def fetch_insights(
        self,
        tokens: TokenSet,
        account_external_id: str,
        date_from: date,
        date_to: date,
        breakdown: str,
    ) -> Iterator[dict]:
        if breakdown != "none":
            raise PermanentError(f"whatsapp: breakdown '{breakdown}' not supported")
        self._require_config()
        names = self._templates(account_external_id)
        ids = list(names.keys())
        start = int(datetime(date_from.year, date_from.month, date_from.day, tzinfo=UTC).timestamp())
        # template_analytics `end` is exclusive → cover the whole date_to day.
        end = int(datetime(date_to.year, date_to.month, date_to.day, tzinfo=UTC).timestamp()) + 86400
        for i in range(0, len(ids), TEMPLATES_PER_CALL):
            chunk = ids[i : i + TEMPLATES_PER_CALL]
            data = self._get(
                f"{self._base}/{account_external_id}/template_analytics",
                {
                    "start": start,
                    "end": end,
                    "granularity": "DAILY",
                    "metric_types": ",".join(METRIC_TYPES),
                    "template_ids": ",".join(chunk),
                },
            )
            for series in data.get("data", []):
                for dp in series.get("data_points", []):
                    tid = str(dp.get("template_id") or series.get("template_id") or "")
                    if not tid:
                        continue
                    yield {
                        "template_id": tid,
                        "template_name": names.get(tid, tid),
                        "start": dp.get("start"),
                        "sent": dp.get("sent"),
                        "delivered": dp.get("delivered"),
                        "read": dp.get("read"),
                        "clicked": dp.get("clicked"),
                    }

    # ---------------------------------------------------- normalization
    def normalize_entity(self, raw: dict, level: Level) -> dict:
        return {}  # unused

    def normalize_insight(self, raw: dict, breakdown: str) -> dict:
        ts = raw.get("start")
        day = (
            datetime.fromtimestamp(int(ts), tz=UTC).date().isoformat()
            if ts else ""
        )
        clicked = raw.get("clicked")
        if isinstance(clicked, list):  # list of {type, button_content, count}
            clicks = sum(int(c.get("count") or 0) for c in clicked)
        else:
            clicks = int(clicked or 0)
        return {
            "date": day,
            "campaign_external_id": raw["template_id"],
            "campaign_name": raw.get("template_name"),
            "adset_external_id": None,
            "ad_external_id": None,
            "breakdown": breakdown,
            "country": None, "device": None, "age": None, "gender": None, "placement": None,
            "spend": 0.0,
            "impressions": int(raw.get("delivered") or 0),   # delivered = reach
            "clicks": clicks,
            "conversions": float(raw.get("read") or 0),       # read receipts
            "conversion_value": 0.0,
            "video_views": 0,
            "leads": 0,
            "purchases": int(raw.get("sent") or 0),           # keep sent visible
        }

    # ------------------------------------------------------------- http
    def _get(self, url: str, params: dict | None) -> dict:
        headers = {"Authorization": f"Bearer {self._cfg.whatsapp_token}"}
        last = "whatsapp: retries exhausted"
        for attempt in range(5):
            try:
                resp = httpx.get(url, headers=headers, params=params, timeout=120)
            except httpx.HTTPError as exc:
                last = f"whatsapp: network error {exc}"
                self._backoff(attempt)
                continue
            if resp.status_code < 400:
                return resp.json()
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"whatsapp: {resp.status_code}"
                self._backoff(attempt)
                continue
            raise PermanentError(f"whatsapp: {resp.status_code} {self._err(resp)}")
        raise TransientError(last)

    @staticmethod
    def _err(resp: httpx.Response) -> str:
        try:
            return str(resp.json().get("error", {}).get("message", ""))[:300]
        except Exception:
            return resp.text[:300]

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 30))
