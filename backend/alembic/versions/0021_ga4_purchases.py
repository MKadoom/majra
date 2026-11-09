"""GA4 purchases: norm.fact_web_performance.purchases

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-09

GA4 `ecommercePurchases` per day/source/medium/campaign: the automatic,
cross-platform reference for donations. Existing rows get 0 until their range
is re-synced (the next scheduled sync restates the last 7 days).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("fact_web_performance", schema="norm")}
    if "purchases" not in cols:
        op.add_column("fact_web_performance",
                      sa.Column("purchases", sa.BigInteger(), nullable=False,
                                server_default="0"),
                      schema="norm")


def downgrade() -> None:
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("fact_web_performance", schema="norm")}
    if "purchases" in cols:
        op.drop_column("fact_web_performance", "purchases", schema="norm")
