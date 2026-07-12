"""phase 7: app.api_keys + app.saved_reports

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-12

Guarded like 0002-0006: no-ops on a fresh DB (the 0001 bootstrap creates the
current metadata), creates the tables on a DB migrated before they existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())

    if not insp.has_table("api_keys", schema="app"):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("prefix", sa.String(length=24), nullable=False, unique=True),
            sa.Column("key_hash", sa.String(length=64), nullable=False),
            sa.Column("scopes", postgresql.JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["app.users.id"]),
            schema="app",
        )
        op.create_index(
            "ix_app_api_keys_prefix", "api_keys", ["prefix"], schema="app", unique=True
        )

    if not insp.has_table("saved_reports", schema="app"):
        op.create_table(
            "saved_reports",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("dataset", sa.String(length=20), nullable=True),
            sa.Column("params", postgresql.JSONB(), nullable=True),
            sa.Column("destination", postgresql.JSONB(), nullable=True),
            sa.Column("cadence", sa.String(length=20), nullable=True),
            sa.Column("hour_utc", sa.Integer(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_status", sa.String(length=20), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["app.users.id"]),
            schema="app",
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("saved_reports", schema="app"):
        op.drop_table("saved_reports", schema="app")
    if insp.has_table("api_keys", schema="app"):
        op.drop_table("api_keys", schema="app")
