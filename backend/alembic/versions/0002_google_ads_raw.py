"""google ads: raw.google_insights table

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08

Note: migration 0001 bootstraps the whole current schema via init_db()/create_all,
so on a brand-new database this table already exists by the time 0002 runs. The
existence guard makes 0002 a no-op there, while still adding the table to any
database that was first migrated at 0001 before this model existed. Later phases
follow this same delta pattern (SPEC §7).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("google_insights", schema="raw"):
        return
    op.create_table(
        "google_insights",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("connection_id", sa.BigInteger(), nullable=False),
        sa.Column("account_external_id", sa.String(length=120), nullable=False),
        sa.Column("params", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        schema="raw",
    )
    op.create_index(
        "ix_raw_google_insights_connection_id",
        "google_insights",
        ["connection_id"],
        schema="raw",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("google_insights", schema="raw"):
        op.drop_table("google_insights", schema="raw")
