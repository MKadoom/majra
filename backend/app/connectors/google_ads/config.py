"""Google Ads connector configuration.

Kept inside the connector package (not app.core.config) so the connector is a
true drop-in: adding it touches nothing in app/core or app/api. Still uses the
MAJRA_ env prefix, consistent with the rest of the project (SPEC §7).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class GoogleAdsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAJRA_", extra="ignore")

    google_client_id: str = ""
    google_client_secret: str = ""
    google_ads_developer_token: str = ""
    # Optional MCC (manager) account id, digits only, sent as login-customer-id.
    google_ads_login_customer_id: str = ""
