"""Connector-scoped settings for WhatsApp Business Management (marketing messages).

Auth is a single long-lived System User token (whatsapp_business_management)
generated in Meta Business Settings — pasted into Railway as MAJRA_WHATSAPP_TOKEN,
never handled in chat. Which WhatsApp Business Accounts (WABAs) to pull is stored
per-connection (so the token stays out of the DB), but a default list can also be
provided via env for convenience.

Pricing: WhatsApp marketing-message templates are billed per *delivered* message,
not as ad spend, so the Graph API reports no cost. We price it ourselves at a
configurable rate (default 0.21 SAR / delivered) so the dashboard shows real
WhatsApp spend instead of zero. Override via env if Meta's rate changes:
    MAJRA_WHATSAPP_PRICE_PER_DELIVERED=0.21
    MAJRA_WHATSAPP_PRICE_CURRENCY=SAR
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhatsAppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAJRA_", extra="ignore")

    whatsapp_token: str = ""
    # Graph API version to call.
    whatsapp_api_version: str = "v21.0"
    # Cost per *delivered* marketing message, in `whatsapp_price_currency`.
    whatsapp_price_per_delivered: float = 0.21
    # Currency the price (and therefore computed WhatsApp spend) is denominated in.
    whatsapp_price_currency: str = "SAR"
