"""GA4 (Google Analytics 4) connector.

Differs from the ad connectors on three axes:
  - Auth is a **service account** (no browser OAuth). The connection stores the
    service-account JSON + a property id; each sync mints a short-lived access
    token via the JWT-bearer grant (signed RS256 with the SA private key).
  - Data is **web analytics** (dataset="web_performance") → fact_web_performance,
    dimensioned by traffic source/medium/campaign, not an ad hierarchy.
  - No entity tables; fetch_entities is unused.

Uses httpx + pyjwt (RS256) so the flow is mockable with respx like every other
connector, and needs no extra dependency.
"""
import time
from datetime import date
from typing import Iterator

import httpx

from app.connectors.base import (
    AccountInfo, BaseConnector, Level, PermanentError, TokenSet, TransientError,
)
from app.connectors.google_sa import mint_access
from app.models import RawGA4Report

DATA_API = "https://analyticsdata.googleapis.com/v1beta"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

# Fixed report shape (positions matter for normalization).
DIMENSIONS = ["date", "sessionSource", "sessionMedium", "sessionCampaignName"]
METRICS = ["sessions", "totalUsers", "conversions", "purchaseRevenue"]
PAGE_LIMIT = 100_000


class GA4Connector(BaseConnector):
    platform = "ga4"
    raw_model = RawGA4Report
    dataset = "web_performance"
    auth_kind = "service_account"

    def __init__(self) -> None:
        self._access: str | None = None

    # ------------------------------------------------- connection setup
    def credentials_from_input(self, payload: dict) -> TokenSet:
        sa = payload.get("service_account")
        prop = payload.get("property_id")
        if not isinstance(sa, dict) or not sa.get("client_email") or not sa.get("private_key"):
            raise PermanentError(
                "ga4: service_account JSON with client_email and private_key required"
            )
        if not prop:
            raise PermanentError("ga4: property_id required")
        return TokenSet(
            access_token="",
            extra={"service_account": sa, "property_id": str(prop)},
        )

    # OAuth methods are not applicable to a service-account connector.
    def auth_url(self, state: str, redirect_uri: str) -> str:
        raise PermanentError("ga4 uses a service account; use /connections/service-account")

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        raise PermanentError("ga4 uses a service account; use /connections/service-account")

    def refresh(self, tokens: TokenSet) -> TokenSet:
        return TokenSet(access_token=self._mint_access(tokens), extra=tokens.extra)

    # -------------------------------------------------------- discovery
    def fetch_accounts(self, tokens: TokenSet) -> list[AccountInfo]:
        # A GA4 connection targets exactly one property (stored at setup).
        prop = tokens.extra.get("property_id")
        if not prop:
            raise PermanentError("ga4: connection missing property_id")
        return [AccountInfo(external_id=str(prop), name=f"GA4 property {prop}")]

    def fetch_entities(
        self, tokens: TokenSet, account_external_id: str, level: Level
    ) -> Iterator[dict]:
        return iter(())  # GA4 has no ad entities

    # ------------------------------------------------------------- data
    def fetch_insights(
        self,
        tokens: TokenSet,
        account_external_id: str,
        date_from: date,
        date_to: date,
        breakdown: str,
    ) -> Iterator[dict]:
        url = f"{DATA_API}/properties/{account_external_id}:runReport"
        offset = 0
        while True:
            body = {
                "dateRanges": [
                    {"startDate": date_from.isoformat(), "endDate": date_to.isoformat()}
                ],
                "dimensions": [{"name": d} for d in DIMENSIONS],
                "metrics": [{"name": m} for m in METRICS],
                "limit": PAGE_LIMIT,
                "offset": offset,
            }
            data = self._request("POST", url, tokens, json=body).json()
            rows = data.get("rows", [])
            for row in rows:
                yield row
            row_count = int(data.get("rowCount", 0))
            offset += len(rows)
            if not rows or offset >= row_count:
                return

    # ---------------------------------------------------- normalization
    def normalize_entity(self, raw: dict, level: Level) -> dict:
        return {}  # unused

    def normalize_insight(self, raw: dict, breakdown: str) -> dict:
        dims = [d.get("value") for d in raw.get("dimensionValues", [])]
        mets = [m.get("value") for m in raw.get("metricValues", [])]

        def dim(i):
            return dims[i] if i < len(dims) else None

        def met(i):
            return mets[i] if i < len(mets) else None

        # GA4 returns the date dimension as YYYYMMDD.
        raw_date = dim(0) or ""
        iso = (
            f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            if len(raw_date) == 8 and raw_date.isdigit()
            else raw_date
        )
        return {
            "date": iso,
            "source": dim(1),
            "medium": dim(2),
            "campaign_name": dim(3),
            "sessions": int(float(met(0) or 0)),
            "users": int(float(met(1) or 0)),
            "conversions": float(met(2) or 0),
            "revenue": float(met(3) or 0),
        }

    # ------------------------------------------------------------- http
    def _mint_access(self, tokens: TokenSet) -> str:
        sa = tokens.extra.get("service_account")
        if not isinstance(sa, dict):
            raise PermanentError("ga4: connection missing service_account")
        return mint_access(sa, SCOPE)

    def _token(self, tokens: TokenSet) -> str:
        if self._access is None:
            self._access = self._mint_access(tokens)
        return self._access

    def _request(
        self, method: str, url: str, tokens: TokenSet, json: dict | None = None
    ) -> httpx.Response:
        last = "ga4: retries exhausted"
        for attempt in range(5):
            try:
                resp = httpx.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {self._token(tokens)}"},
                    json=json,
                    timeout=120,
                )
            except httpx.HTTPError as exc:
                last = f"ga4: network error {exc}"
                self._backoff(attempt)
                continue
            if resp.status_code < 400:
                return resp
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"ga4: {resp.status_code}"
                self._backoff(attempt)
                continue
            raise PermanentError(f"ga4: {resp.status_code} {self._err(resp)}")
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
