"""tiktok + snapchat raw tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08

Guarded like 0002/0003: no-ops on a fresh DB (0001 bootstrap already made these
via create_all), creates them on a DB migrated before these models existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_RAW_TABLES = ["tiktok_reports", "snapchat_stats"]


def _create_raw(name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("connection_id", sa.BigInteger(), nullable=False),
        sa.Column("account_external_id", sa.String(length=120), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        schema="raw",
    )
    op.create_index(
        f"ix_raw_{name}_connection_id", name, ["connection_id"], schema="raw"
    )


def upgrade() -> None:
    insp = inspect(op.get_bind())
    for name in _RAW_TABLES:
        if not insp.has_table(name, schema="raw"):
            _create_raw(name)


def downgrade() -> None:
    insp = inspect(op.get_bind())
    for name in _RAW_TABLES:
        if insp.has_table(name, schema="raw"):
            op.drop_table(name, schema="raw")
