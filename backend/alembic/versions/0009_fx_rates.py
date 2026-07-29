"""currency normalization: app.fx_rates

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-29

Guarded like 0002-0008: a no-op on a fresh DB (the 0001 bootstrap creates the
current metadata via create_all), creates the table on a DB migrated before it
existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not insp.has_table("fx_rates", schema="app"):
        op.create_table(
            "fx_rates",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("currency", sa.String(length=8), nullable=False, unique=True),
            sa.Column("rate_to_usd", sa.Numeric(18, 8), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            schema="app",
        )
        op.create_index(
            "ix_app_fx_rates_currency", "fx_rates", ["currency"], schema="app", unique=True
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("fx_rates", schema="app"):
        op.drop_index("ix_app_fx_rates_currency", table_name="fx_rates", schema="app")
        op.drop_table("fx_rates", schema="app")
