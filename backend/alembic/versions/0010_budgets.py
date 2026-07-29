"""budget pacing: app.budgets

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-29

Guarded like 0002-0009: a no-op on a fresh DB (the 0001 bootstrap creates the
current metadata via create_all), creates the table on a DB migrated before it
existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not insp.has_table("budgets", schema="app"):
        op.create_table(
            "budgets",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("platform", sa.String(length=50), nullable=True),
            sa.Column("account_id", sa.BigInteger(), nullable=True),
            sa.Column("amount_monthly", sa.Numeric(18, 2), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["app.users.id"]),
            sa.ForeignKeyConstraint(["account_id"], ["norm.accounts.id"]),
            schema="app",
        )
        op.create_index(
            "ix_app_budgets_user_id", "budgets", ["user_id"], schema="app"
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("budgets", schema="app"):
        op.drop_index("ix_app_budgets_user_id", table_name="budgets", schema="app")
        op.drop_table("budgets", schema="app")
