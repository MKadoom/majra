"""majra settings. All env vars are prefixed MAJRA_ (SPEC §7)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAJRA_", env_file=".env", extra="ignore"
    )

    environment: str = "dev"  # dev | test | production
    database_url: str = "postgresql+psycopg://majra:majra@localhost:5432/majra"
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    secret_key: str = "change-me-in-production"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # Token encryption at rest (Fernet). Generate:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    # OAuth callbacks are built from this (must be reachable by the browser).
    public_base_url: str = "http://localhost:8000"

    # Meta (Phase 0 output)
    meta_app_id: str = ""
    meta_app_secret: str = ""

    # Optional alert webhook on final sync failure
    alert_webhook_url: str = ""

    # Exports: a hard cap so a careless query can't exhaust memory.
    export_max_rows: int = 200_000

    # --- hardening (Phase 8) ---
    # Browser origins allowed to call the API directly. EMPTY BY DEFAULT: the
    # dashboard proxies server-side and needs none. Only add an origin if you're
    # building a separate browser app against an API key.
    cors_origins: list[str] = []

    # Rate limits (slowapi syntax). Auth is the one endpoint worth throttling
    # hard — it's where credential stuffing lands.
    rate_limit_auth: str = "10/minute"
    rate_limit_read: str = "120/minute"
    # "memory://" is per-process. With more than one worker, point this at redis
    # (e.g. redis://redis:6379/1) so the limit is shared.
    rate_limit_storage: str = "memory://"
    rate_limit_enabled: bool = True

    # Prometheus scrape endpoint. NOTE: it is /_metrics, not /metrics — /metrics
    # is the ad-performance query API.
    metrics_enabled: bool = True
    # Send HSTS. Only turn this on when you're actually behind TLS.
    hsts_enabled: bool = False

    # SMTP, for emailed saved reports. Leave host blank to disable email delivery.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "majra@localhost"
    smtp_starttls: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
