"""social: norm.fact_social_performance + raw.meta_social

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-08

Guarded like 0002-0004: no-ops on a fresh DB (0001 bootstrap already made these),
creates them on a DB migrated before these models existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())

    if not insp.has_table("fact_social_performance", schema="norm"):
        op.create_table(
            "fact_social_performance",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("account_id", sa.BigInteger(), nullable=False),
            sa.Column("grain", sa.String(length=30), nullable=True),
            sa.Column("external_ref", sa.String(length=120), nullable=True),
            sa.Column("impressions", sa.BigInteger(), nullable=True),
            sa.Column("reach", sa.BigInteger(), nullable=True),
            sa.Column("engagement", sa.BigInteger(), nullable=True),
            sa.Column("followers", sa.BigInteger(), nullable=True),
            sa.ForeignKeyConstraint(["account_id"], ["norm.accounts.id"]),
            schema="norm",
        )
        op.create_index(
            "ix_social_date_account", "fact_social_performance",
            ["date", "account_id"], schema="norm",
        )

    if not insp.has_table("meta_social", schema="raw"):
        op.create_table(
            "meta_social",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("connection_id", sa.BigInteger(), nullable=False),
            sa.Column("account_external_id", sa.String(length=120), nullable=False),
            sa.Column("params", postgresql.JSONB(), nullable=True),
            sa.Column("payload", postgresql.JSONB(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
            schema="raw",
        )
        op.create_index(
            "ix_raw_meta_social_connection_id", "meta_social",
            ["connection_id"], schema="raw",
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("meta_social", schema="raw"):
        op.drop_table("meta_social", schema="raw")
    if insp.has_table("fact_social_performance", schema="norm"):
        op.drop_table("fact_social_performance", schema="norm")
