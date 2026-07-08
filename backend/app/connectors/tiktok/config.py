"""Connector-scoped settings for TikTok Ads (keeps core/config.py untouched)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class TikTokSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAJRA_", extra="ignore")

    tiktok_app_id: str = ""
    tiktok_app_secret: str = ""
