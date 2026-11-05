"""Connector-scoped settings for WhatsApp Business Management (marketing messages).

Auth is a single long-lived System User token (whatsapp_business_management)
generated in Meta Business Settings — pasted into Railway as MAJRA_WHATSAPP_TOKEN,
never handled in chat. Which WhatsApp Business Accounts (WABAs) to pull is stored
per-connection (so the token stays out of the DB), but a default list can also be
provided via env for convenience.

Pricing (T6): spend comes from Meta's `pricing_analytics` cost. The rate below
is only a FALLBACK for days Meta returns no cost for — those rows are marked
cost_source="estimate" and /metrics warns about them. Override via env if
Meta's rate changes:
    MAJRA_WHATSAPP_PRICE_PER_DELIVERED=0.21
    MAJRA_WHATSAPP_PRICE_CURRENCY=SAR
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhatsAppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAJRA_", extra="ignore")

    whatsapp_token: str = ""
    # Graph API version to call.
    whatsapp_api_version: str = "v26.0"
    # Cost per *delivered* marketing message, in `whatsapp_price_currency`.
    whatsapp_price_per_delivered: float = 0.21
    # Currency the price (and therefore computed WhatsApp spend) is denominated in.
    whatsapp_price_currency: str = "SAR"
