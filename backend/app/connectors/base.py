"""Connector SDK (SPEC §2).

Every connector subclasses BaseConnector, sets `platform`, and is auto-registered.
Connectors raise only TransientError (retryable) or PermanentError. Nothing else
escapes a connector. Normalization functions are pure and fixture-testable.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import ClassVar, Iterator, Literal

Level = Literal["campaign", "adset", "ad"]

# Breakdown sets a connector may support. 'none' is mandatory.
BREAKDOWN_SETS = ("none", "country", "device", "age_gender")


class TransientError(Exception):
    """Retryable: rate limit, 5xx, timeout."""


class PermanentError(Exception):
    """Not retryable: revoked auth, invalid request. Sync fails immediately."""


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None = None
    expires_at: str | None = None  # ISO-8601 UTC
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TokenSet":
        return cls(
            access_token=d["access_token"],
            refresh_token=d.get("refresh_token"),
            expires_at=d.get("expires_at"),
            extra=d.get("extra") or {},
        )


@dataclass
class AccountInfo:
    external_id: str
    name: str
    currency: str | None = None
    timezone: str | None = None


CONNECTORS: dict[str, type["BaseConnector"]] = {}


class BaseConnector(ABC):
    platform: ClassVar[str] = ""
    # SQLAlchemy model for this platform's append-only raw layer, or None to
    # skip raw capture. Declared here so the sync engine needs no per-connector
    # edits (SPEC §3). The model itself lives in app.models so it registers in
    # the metadata for migrations.
    raw_model: ClassVar[type | None] = None
    # Which sync shape / fact table this connector feeds. The engine dispatches
    # on this (see app.services.sync.DATASET_HANDLERS). Ad platforms use the
    # default; GA4 uses "web_performance".
    dataset: ClassVar[str] = "ad_performance"
    # How connections are created: "oauth" (browser flow via /connect/{platform})
    # or "service_account" (credentials posted to /connections/service-account).
    auth_kind: ClassVar[str] = "oauth"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.platform:
            CONNECTORS[cls.platform] = cls

    def credentials_from_input(self, payload: dict) -> "TokenSet":
        """Build the stored token blob for a service-account connection from
        user-supplied credentials. OAuth connectors don't use this."""
        raise PermanentError(
            f"{self.platform or 'this platform'} uses OAuth; connect via /connect"
        )

    def snapshot(self, tokens: "TokenSet", account_external_id: str) -> "Iterator[dict]":
        """Yield config records for a `config_snapshot` dataset (e.g. GTM):
        dicts with a `kind` plus its fields. Fact connectors don't use this."""
        raise PermanentError(f"{self.platform or 'this platform'} has no config snapshot")

    # ------------------------------------------------------------- auth
    @abstractmethod
    def auth_url(self, state: str, redirect_uri: str) -> str: ...

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet: ...

    @abstractmethod
    def refresh(self, tokens: TokenSet) -> TokenSet: ...

    # -------------------------------------------------------- discovery
    @abstractmethod
    def fetch_accounts(self, tokens: TokenSet) -> list[AccountInfo]: ...

    # ------------------------------------------------------------- data
    @abstractmethod
    def fetch_entities(
        self, tokens: TokenSet, account_external_id: str, level: Level
    ) -> Iterator[dict]: ...

    @abstractmethod
    def fetch_insights(
        self,
        tokens: TokenSet,
        account_external_id: str,
        date_from: date,
        date_to: date,
        breakdown: str,
    ) -> Iterator[dict]: ...

    # ---------------------------------------------------- normalization
    @abstractmethod
    def normalize_entity(self, raw: dict, level: Level) -> dict: ...

    @abstractmethod
    def normalize_insight(self, raw: dict, breakdown: str) -> dict: ...


def get_connector(platform: str) -> BaseConnector:
    try:
        return CONNECTORS[platform]()
    except KeyError:
        raise PermanentError(f"unknown platform: {platform}")


def utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"
