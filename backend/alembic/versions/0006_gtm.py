"""gtm: norm.gtm_resources + raw.gtm_snapshots

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-08

Guarded like 0002-0005: no-ops on a fresh DB, creates the tables on a DB migrated
before these models existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())

    if not insp.has_table("gtm_resources", schema="norm"):
        op.create_table(
            "gtm_resources",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("account_id", sa.BigInteger(), nullable=False),
            sa.Column("kind", sa.String(length=30), nullable=True),
            sa.Column("container_external_id", sa.String(length=120), nullable=True),
            sa.Column("workspace_external_id", sa.String(length=120), nullable=True),
            sa.Column("external_id", sa.String(length=120), nullable=True),
            sa.Column("name", sa.String(length=400), nullable=True),
            sa.Column("type", sa.String(length=120), nullable=True),
            sa.Column("data", postgresql.JSONB(), nullable=True),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["account_id"], ["norm.accounts.id"]),
            schema="norm",
        )
        op.create_index(
            "ix_gtm_account_captured", "gtm_resources",
            ["account_id", "captured_at"], schema="norm",
        )

    if not insp.has_table("gtm_snapshots", schema="raw"):
        op.create_table(
            "gtm_snapshots",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("connection_id", sa.BigInteger(), nullable=False),
            sa.Column("account_external_id", sa.String(length=120), nullable=False),
            sa.Column("params", postgresql.JSONB(), nullable=True),
            sa.Column("payload", postgresql.JSONB(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
            schema="raw",
        )
        op.create_index(
            "ix_raw_gtm_snapshots_connection_id", "gtm_snapshots",
            ["connection_id"], schema="raw",
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("gtm_snapshots", schema="raw"):
        op.drop_table("gtm_snapshots", schema="raw")
    if insp.has_table("gtm_resources", schema="norm"):
        op.drop_table("gtm_resources", schema="norm")
