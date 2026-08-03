"""Connector-scoped settings for WhatsApp Business Management (marketing messages).

Auth is a single long-lived System User token (whatsapp_business_management)
generated in Meta Business Settings — pasted into Railway as MAJRA_WHATSAPP_TOKEN,
never handled in chat. Which WhatsApp Business Accounts (WABAs) to pull is stored
per-connection (so the token stays out of the DB), but a default list can also be
provided via env for convenience.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhatsAppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAJRA_", extra="ignore")

    whatsapp_token: str = ""
    # Graph API version to call.
    whatsapp_api_version: str = "v21.0"
