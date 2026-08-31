"""WhatsApp Business Management connector — marketing-message analytics.

Not an ad platform: this reads the WhatsApp Business Management API's **messaging
analytics** (`/{waba}?fields=analytics…`) — the same "Messages delivered" figure
WhatsApp Manager shows — and maps it onto the ad_performance fact shape:

    delivered  -> impressions   (the headline "reach" / messages delivered)
    sent       -> purchases     (kept visible)
    spend       = delivered * price_per_delivered  (WhatsApp bills marketing
                                messages per delivered message, not as ad
                                spend, so we price it — default 0.21 SAR —
                                to show real cost instead of zero)

Account-level per day (per WhatsApp Business Account). Per-*template* analytics
(`template_analytics`) was tried first but returns zeros for MM_LITE broadcast
sends, so messaging analytics is the reliable source; the template *count* (each
template ≈ a campaign) is still available via `message_templates`.

Auth is a single System User token (whatsapp_business_management) read from env
(MAJRA_WHATSAPP_TOKEN); a connection stores which WABAs to pull in tokens.extra.
`insights_carry_hierarchy` is True — each row carries a synthetic per-WABA
campaign, so the engine skips the entity walk.
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


class WhatsAppConnector(BaseConnector):
    platform = "whatsapp"
    raw_model = None                       # no raw table → no migration needed
    dataset = "ad_performance"
    auth_kind = "service_account"
    insights_carry_hierarchy = True        # synthetic campaign; no entity walk

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
                    # Spend is computed as delivered * price, denominated in the
                    # pricing currency — so the fact row must carry that currency,
                    # not whatever billing currency the WABA reports.
                    currency=self._cfg.whatsapp_price_currency,
                )
            )
        return out

    def fetch_entities(
        self, tokens: TokenSet, account_external_id: str, level: Level
    ) -> Iterator[dict]:
        return iter(())  # synthetic campaign comes through on the insight rows

    def template_count(self, waba: str) -> int:
        """How many message templates the WABA has (each ≈ a marketing campaign)."""
        n, url, params = 0, f"{self._base}/{waba}/message_templates", {"fields": "id", "limit": 200}
        while True:
            data = self._get(url, params)
            n += len(data.get("data", []))
            nxt = (data.get("paging") or {}).get("next")
            if not nxt:
                return n
            url, params = nxt, None

    # ------------------------------------------------------------- data
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
        start = int(datetime(date_from.year, date_from.month, date_from.day, tzinfo=UTC).timestamp())
        end = int(datetime(date_to.year, date_to.month, date_to.day, tzinfo=UTC).timestamp()) + 86400
        field = f"analytics.start({start}).end({end}).granularity(DAY)"
        data = self._get(f"{self._base}/{account_external_id}", {"fields": field})
        analytics = data.get("analytics") or {}
        for dp in analytics.get("data_points", []):
            yield {
                "waba": account_external_id,
                "start": dp.get("start"),
                "sent": dp.get("sent"),
                "delivered": dp.get("delivered"),
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
        delivered = int(raw.get("delivered") or 0)
        # WhatsApp bills marketing messages per delivered message; the Graph API
        # returns no cost, so price it ourselves (default 0.21 SAR / delivered).
        spend = round(delivered * self._cfg.whatsapp_price_per_delivered, 4)
        return {
            "date": day,
            "campaign_external_id": f"wa-messages-{raw.get('waba')}",
            "campaign_name": "WhatsApp Marketing Messages",
            "adset_external_id": None,
            "ad_external_id": None,
            "breakdown": breakdown,
            "country": None, "device": None, "age": None, "gender": None, "placement": None,
            "spend": spend,                                  # delivered * price
            "impressions": delivered,                        # messages delivered
            "clicks": 0,
            "conversions": 0.0,
            "conversion_value": 0.0,
            "video_views": 0,
            "leads": 0,
            "purchases": int(raw.get("sent") or 0),           # messages sent
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
