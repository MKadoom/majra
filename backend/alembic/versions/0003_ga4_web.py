"""ga4: norm.fact_web_performance + raw.ga4_reports

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08

Guarded like 0002: on a fresh DB the 0001 bootstrap (create_all) already made
these tables, so this migration no-ops there; on a DB first migrated before
these models existed, it creates them.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())

    if not insp.has_table("fact_web_performance", schema="norm"):
        op.create_table(
            "fact_web_performance",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("account_id", sa.BigInteger(), nullable=False),
            sa.Column("source", sa.String(length=200), nullable=True),
            sa.Column("medium", sa.String(length=200), nullable=True),
            sa.Column("campaign_name", sa.String(length=400), nullable=True),
            sa.Column("sessions", sa.BigInteger(), nullable=True),
            sa.Column("users", sa.BigInteger(), nullable=True),
            sa.Column("conversions", sa.Numeric(18, 6), nullable=True),
            sa.Column("revenue", sa.Numeric(18, 6), nullable=True),
            sa.ForeignKeyConstraint(["account_id"], ["norm.accounts.id"]),
            schema="norm",
        )
        op.create_index(
            "ix_web_date_account", "fact_web_performance",
            ["date", "account_id"], schema="norm",
        )

    if not insp.has_table("ga4_reports", schema="raw"):
        op.create_table(
            "ga4_reports",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("connection_id", sa.BigInteger(), nullable=False),
            sa.Column("account_external_id", sa.String(length=120), nullable=False),
            sa.Column("params", postgresql.JSONB(), nullable=True),
            sa.Column("payload", postgresql.JSONB(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
            schema="raw",
        )
        op.create_index(
            "ix_raw_ga4_reports_connection_id", "ga4_reports",
            ["connection_id"], schema="raw",
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("ga4_reports", schema="raw"):
        op.drop_table("ga4_reports", schema="raw")
    if insp.has_table("fact_web_performance", schema="norm"):
        op.drop_table("fact_web_performance", schema="norm")
