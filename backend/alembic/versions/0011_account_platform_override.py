"""per-account platform label override: norm.accounts.platform_override

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-29

Guarded like 0002-0010: a no-op on a fresh DB (the 0001 bootstrap creates the
current metadata via create_all), adds the column on a DB migrated before it
existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str, schema: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table, schema=schema))


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("accounts", schema="norm") and not _has_column(
        insp, "accounts", "platform_override", "norm"
    ):
        op.add_column(
            "accounts",
            sa.Column("platform_override", sa.String(length=50), nullable=True),
            schema="norm",
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("accounts", schema="norm") and _has_column(
        insp, "accounts", "platform_override", "norm"
    ):
        op.drop_column("accounts", "platform_override", schema="norm")
