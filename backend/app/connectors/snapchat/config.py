"""Connector-scoped settings for Snapchat Ads (keeps core/config.py untouched)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class SnapchatSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAJRA_", extra="ignore")

    snap_client_id: str = ""
    snap_client_secret: str = ""
