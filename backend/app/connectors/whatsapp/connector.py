"""WhatsApp Business Management connector — marketing-message analytics + cost.

Not an ad platform. Two WhatsApp Business Management API fields per WABA:

- `analytics` (messaging analytics): messages **sent** / **delivered** — the
  figures WhatsApp Manager shows → `messages_sent` / `messages_delivered`
  (SPEC §5a: never purchases or impressions).
- `pricing_analytics`: Meta's **cost** per period ("approximate charges for
  messages delivered, in your Messaging account's currency") → `spend`, with
  `cost_source = "pricing_analytics"` (T6).

Days are **Riyadh days** (UTC+3): both fields are requested at HALF_HOUR
granularity over a window aligned to Riyadh midnight and summed per Riyadh date,
so a day's figures don't straddle two calendar days. If Meta refuses HALF_HOUR
for the range, DAILY is used instead (Meta's day boundaries; logged in the row).

When pricing data is unavailable for a day (e.g. an account on a partner's
credit line, which Meta documents as returning no cost), spend falls back to an
estimate — delivered × MAJRA_WHATSAPP_PRICE_PER_DELIVERED — marked
`cost_source = "estimate"`, but only if that price is in the account's
currency; otherwise spend is 0 with `cost_source = "missing"`. /metrics warns
about both, so an estimate is never mistaken for a bill.

Auth is a single System User token (whatsapp_business_management) read from env
(MAJRA_WHATSAPP_TOKEN); a connection stores which WABAs to pull in tokens.extra.
`insights_carry_hierarchy` is True — each row carries a synthetic per-WABA
campaign, so the engine skips the entity walk.
"""
import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

import httpx

from app.connectors.base import (
    AccountInfo,
    AuthError,
    BaseConnector,
    Level,
    PermanentError,
    TokenSet,
    TransientError,
)
from app.connectors.whatsapp.config import WhatsAppSettings

# Saudi Arabia has no DST: a fixed offset is exact (and needs no tzdata).
RIYADH = timezone(timedelta(hours=3), "Asia/Riyadh")


def riyadh_midnight_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=RIYADH).timestamp())


def riyadh_day(ts: int | str) -> date:
    return datetime.fromtimestamp(int(ts), tz=RIYADH).date()


class WhatsAppConnector(BaseConnector):
    platform = "whatsapp"
    raw_model = None                       # no raw table → no migration needed
    dataset = "ad_performance"
    auth_kind = "service_account"
    insights_carry_hierarchy = True        # synthetic campaign; no entity walk

    def __init__(self) -> None:
        self._cfg = WhatsAppSettings()
        self._base = f"https://graph.facebook.com/{self._cfg.whatsapp_api_version}"
        self._currency: dict[str, str] = {}  # waba -> account currency

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
                    # pricing_analytics cost is in the WABA's own currency, so
                    # the fact rows carry that. Fall back to the price currency
                    # only if Meta doesn't report one.
                    currency=(info.get("currency")
                              or self._cfg.whatsapp_price_currency).upper(),
                )
            )
            self._currency[str(waba)] = out[-1].currency
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
        start = riyadh_midnight_ts(date_from)
        end = riyadh_midnight_ts(date_to + timedelta(days=1))
        try:
            msgs, cost, granularity = self._pull(account_external_id, start, end, "HALF_HOUR")
        except PermanentError:
            # Meta may not serve half-hour points for older ranges: use its days.
            msgs, cost, granularity = self._pull(account_external_id, start, end, "DAILY")

        day = date_from
        while day <= date_to:
            m = msgs.get(day, {})
            c = cost.get(day)
            if m or c is not None:
                yield {
                    "waba": account_external_id,
                    "day": day.isoformat(),
                    "sent": m.get("sent", 0),
                    "delivered": m.get("delivered", 0),
                    "cost": c,                       # None = Meta returned no cost
                    "currency": self._currency.get(account_external_id),
                    "granularity": granularity,
                }
            day += timedelta(days=1)

    def _pull(self, waba: str, start: int, end: int, granularity: str):
        """Sum messaging analytics and pricing analytics per Riyadh day."""
        msg_gran = "HALF_HOUR" if granularity == "HALF_HOUR" else "DAY"
        data = self._get(f"{self._base}/{waba}", {
            "fields": f"analytics.start({start}).end({end}).granularity({msg_gran})"})
        msgs: dict[date, dict] = {}
        for dp in (data.get("analytics") or {}).get("data_points", []):
            if dp.get("start") is None:
                continue
            d = msgs.setdefault(riyadh_day(dp["start"]), {"sent": 0, "delivered": 0})
            d["sent"] += int(dp.get("sent") or 0)
            d["delivered"] += int(dp.get("delivered") or 0)

        pdata = self._get(f"{self._base}/{waba}", {
            "fields": (f"pricing_analytics.start({start}).end({end})"
                       f".granularity({granularity})"
                       '.dimensions(["PRICING_CATEGORY","PRICING_TYPE"])')})
        cost: dict[date, float] = {}
        for block in (pdata.get("pricing_analytics") or {}).get("data", []):
            for dp in block.get("data_points", []):
                if dp.get("start") is None or dp.get("cost") is None:
                    continue
                d = riyadh_day(dp["start"])
                cost[d] = cost.get(d, 0.0) + float(dp["cost"])
        return msgs, cost, granularity

    # ---------------------------------------------------- normalization
    def normalize_entity(self, raw: dict, level: Level) -> dict:
        return {}  # unused

    def normalize_insight(self, raw: dict, breakdown: str) -> dict:
        delivered = int(raw.get("delivered") or 0)
        cost = raw.get("cost")
        if cost is not None:
            spend, source = round(float(cost), 4), "pricing_analytics"
        elif (raw.get("currency") or self._cfg.whatsapp_price_currency).upper() \
                == self._cfg.whatsapp_price_currency.upper():
            # No cost from Meta for this day: estimate, clearly marked.
            spend = round(delivered * self._cfg.whatsapp_price_per_delivered, 4)
            source = "estimate"
        else:
            # The configured price is in another currency than the account's —
            # an estimate would be in the wrong unit. Report nothing, say so.
            spend, source = 0.0, "missing"
        return {
            "date": raw.get("day") or "",
            "campaign_external_id": f"wa-messages-{raw.get('waba')}",
            "campaign_name": "WhatsApp Marketing Messages",
            "adset_external_id": None,
            "ad_external_id": None,
            "breakdown": breakdown,
            "country": None, "device": None, "age": None, "gender": None, "placement": None,
            "spend": spend,
            "cost_source": source,
            # Messages are not ad impressions or purchases (SPEC §5a).
            "impressions": 0,
            "clicks": 0,
            "conversions": 0.0,
            "conversion_value": 0.0,
            "video_views": 0,
            "leads": 0,
            "purchases": 0,
            "messages_sent": int(raw.get("sent") or 0),
            "messages_delivered": delivered,
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
            if resp.status_code == 401 or self._is_token_error(resp):
                raise AuthError(f"whatsapp: {resp.status_code} {self._err(resp)}")
            raise PermanentError(f"whatsapp: {resp.status_code} {self._err(resp)}")
        raise TransientError(last)

    @staticmethod
    def _is_token_error(resp: httpx.Response) -> bool:
        """Graph error 190 (invalid/expired token) or 102 (session)."""
        try:
            return resp.json().get("error", {}).get("code") in (102, 190)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _err(resp: httpx.Response) -> str:
        try:
            return str(resp.json().get("error", {}).get("message", ""))[:300]
        except Exception:
            return resp.text[:300]

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 30))
