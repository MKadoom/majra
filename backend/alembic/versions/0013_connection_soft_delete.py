"""soft-delete connections: app.connections.deleted_at

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-29

Guarded like 0002-0012: a no-op on a fresh DB (the 0001 bootstrap creates the
current metadata via create_all), adds the column on a DB migrated before it
existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str, schema: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table, schema=schema))


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("connections", schema="app") and not _has_column(
        insp, "connections", "deleted_at", "app"
    ):
        op.add_column(
            "connections",
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            schema="app",
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("connections", schema="app") and _has_column(
        insp, "connections", "deleted_at", "app"
    ):
        op.drop_column("connections", "deleted_at", schema="app")
