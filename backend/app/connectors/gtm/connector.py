"""Google Tag Manager connector.

dataset="config_snapshot" — GTM has no daily metrics; a sync captures the current
config (containers → workspaces → tags/triggers/variables) as a point-in-time
snapshot, appended with one captured_at (audit history). Service-account auth
(shared with GA4). The engine's config handler stores each yielded record; there
is no date range and no delete+insert.
"""
import random
import time
from collections.abc import Iterator

import httpx

from app.connectors.base import (
    AccountInfo,
    BaseConnector,
    Level,
    PermanentError,
    TokenSet,
    TransientError,
)
from app.connectors.google_sa import mint_access
from app.models import RawGtmSnapshot

API = "https://www.googleapis.com/tagmanager/v2"
SCOPE = "https://www.googleapis.com/auth/tagmanager.readonly"

# GTM list endpoint -> (response key, id field, whether it has a type)
_RESOURCE_QUERIES = [
    ("tags", "tag", "tagId", "tag"),
    ("triggers", "trigger", "triggerId", "trigger"),
    ("variables", "variable", "variableId", "variable"),
]


class GTMConnector(BaseConnector):
    platform = "gtm"
    raw_model = RawGtmSnapshot
    dataset = "config_snapshot"
    auth_kind = "service_account"

    def __init__(self) -> None:
        self._access: str | None = None
        self._account_filter: str | None = None

    # ------------------------------------------------- connection setup
    def credentials_from_input(self, payload: dict) -> TokenSet:
        sa = payload.get("service_account")
        if not isinstance(sa, dict) or not sa.get("client_email") or not sa.get("private_key"):
            raise PermanentError(
                "gtm: service_account JSON with client_email and private_key required"
            )
        # property_id is optional here and interpreted as an account-id filter.
        extra = {"service_account": sa}
        if payload.get("property_id"):
            extra["account_id"] = str(payload["property_id"])
        return TokenSet(access_token="", extra=extra)

    def auth_url(self, state: str, redirect_uri: str) -> str:
        raise PermanentError("gtm uses a service account; use /connections/service-account")

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        raise PermanentError("gtm uses a service account; use /connections/service-account")

    def refresh(self, tokens: TokenSet) -> TokenSet:
        return TokenSet(access_token=self._mint(tokens), extra=tokens.extra)

    # -------------------------------------------------------- discovery
    def fetch_accounts(self, tokens: TokenSet) -> list[AccountInfo]:
        self._account_filter = tokens.extra.get("account_id")
        accounts = self._list(f"{API}/accounts", tokens, "account")
        out = []
        for a in accounts:
            aid = str(a.get("accountId"))
            if self._account_filter and aid != self._account_filter:
                continue
            out.append(AccountInfo(external_id=aid, name=a.get("name") or aid))
        return out

    def fetch_entities(self, tokens, account_external_id, level: Level) -> Iterator[dict]:
        return iter(())  # not used for config snapshots

    def fetch_insights(self, tokens, account_external_id, date_from, date_to, breakdown):
        return iter(())  # not used for config snapshots

    def normalize_entity(self, raw: dict, level: Level) -> dict:
        return {}

    def normalize_insight(self, raw: dict, breakdown: str) -> dict:
        return {}

    # ------------------------------------------------------- snapshot
    def snapshot(self, tokens: TokenSet, account_external_id: str) -> Iterator[dict]:
        acct = account_external_id
        containers = self._list(f"{API}/accounts/{acct}/containers", tokens, "container")
        for c in containers:
            cid = str(c.get("containerId"))
            yield {
                "kind": "container", "external_id": cid, "name": c.get("name"),
                "type": "container", "container_external_id": cid,
                "data": {"public_id": c.get("publicId")},
            }
            workspaces = self._list(
                f"{API}/accounts/{acct}/containers/{cid}/workspaces", tokens, "workspace"
            )
            for w in workspaces:
                wid = str(w.get("workspaceId"))
                yield {
                    "kind": "workspace", "external_id": wid, "name": w.get("name"),
                    "container_external_id": cid, "workspace_external_id": wid,
                }
                base = f"{API}/accounts/{acct}/containers/{cid}/workspaces/{wid}"
                for path, key, id_field, kind in _RESOURCE_QUERIES:
                    for r in self._list(f"{base}/{path}", tokens, key):
                        yield {
                            "kind": kind,
                            "external_id": str(r.get(id_field)),
                            "name": r.get("name"),
                            "type": r.get("type"),
                            "container_external_id": cid,
                            "workspace_external_id": wid,
                        }

    # ------------------------------------------------------------- http
    def _mint(self, tokens: TokenSet) -> str:
        sa = tokens.extra.get("service_account")
        if not isinstance(sa, dict):
            raise PermanentError("gtm: connection missing service_account")
        return mint_access(sa, SCOPE)

    def _token(self, tokens: TokenSet) -> str:
        if self._access is None:
            self._access = self._mint(tokens)
        return self._access

    def _list(self, url: str, tokens: TokenSet, key: str) -> list[dict]:
        """GET a GTM list endpoint, following nextPageToken."""
        out: list[dict] = []
        params: dict = {}
        while True:
            data = self._get(url, tokens, params)
            out.extend(data.get(key, []))
            token = data.get("nextPageToken")
            if not token:
                return out
            params = {"pageToken": token}

    def _get(self, url: str, tokens: TokenSet, params: dict | None = None) -> dict:
        last = "gtm: retries exhausted"
        for attempt in range(5):
            try:
                resp = httpx.get(
                    url,
                    headers={"Authorization": f"Bearer {self._token(tokens)}"},
                    params=params,
                    timeout=120,
                )
            except httpx.HTTPError as exc:
                last = f"gtm: network error {exc}"
                self._backoff(attempt)
                continue
            if resp.status_code < 400:
                return resp.json()
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"gtm: {resp.status_code}"
                self._backoff(attempt)
                continue
            raise PermanentError(f"gtm: {resp.status_code} {resp.text[:200]}")
        raise TransientError(last)

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 30) + random.random())
