"""daily reconciliation results: app.reconciliation_results (T7)

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-03
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not insp.has_table("reconciliation_results", schema="app"):
        op.create_table(
            "reconciliation_results",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("run_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("connection_id", sa.BigInteger(),
                      sa.ForeignKey("app.connections.id"), nullable=False),
            sa.Column("account_id", sa.BigInteger(),
                      sa.ForeignKey("norm.accounts.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("metric", sa.String(30), nullable=False),
            sa.Column("platform_value", sa.Numeric(18, 6), nullable=True),
            sa.Column("stored_value", sa.Numeric(18, 6), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            schema="app",
        )
        op.create_index("ix_reconciliation_results_run_at", "reconciliation_results",
                        ["run_at"], schema="app")
        op.create_index("ix_reconciliation_results_connection_id",
                        "reconciliation_results", ["connection_id"], schema="app")


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("reconciliation_results", schema="app"):
        op.drop_table("reconciliation_results", schema="app")
