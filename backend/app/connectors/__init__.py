# Importing a connector module registers it in CONNECTORS (base.__init_subclass__).
from app.connectors.base import CONNECTORS, get_connector  # noqa: F401
from app.connectors.meta.connector import MetaConnector  # noqa: F401
from app.connectors.google_ads.connector import GoogleAdsConnector  # noqa: F401
from app.connectors.ga4.connector import GA4Connector  # noqa: F401
