"""connection account allow-list: app.connections.synced_account_ids

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-28

Guarded like 0002-0007: a no-op on a fresh DB (the 0001 bootstrap creates the
current metadata via create_all), adds the column on a DB migrated before it
existed.
"""
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str, schema: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table, schema=schema))


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("connections", schema="app") and not _has_column(
        insp, "connections", "synced_account_ids", "app"
    ):
        op.add_column(
            "connections",
            sa.Column("synced_account_ids", postgresql.JSONB(), nullable=True),
            schema="app",
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("connections", schema="app") and _has_column(
        insp, "connections", "synced_account_ids", "app"
    ):
        op.drop_column("connections", "synced_account_ids", schema="app")
