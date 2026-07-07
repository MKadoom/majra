# Importing a connector module registers it in CONNECTORS (base.__init_subclass__).
from app.connectors.base import CONNECTORS, get_connector  # noqa: F401
from app.connectors.meta.connector import MetaConnector  # noqa: F401
