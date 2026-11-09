"""Jood donation orders (ground truth): app.donation_orders

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("donation_orders", schema="app"):
        return
    op.create_table(
        "donation_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("app.users.id"), nullable=False),
        sa.Column("source", sa.String(30), nullable=False, server_default="jood"),
        sa.Column("external_id", sa.String(120), nullable=False),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="SAR"),
        sa.Column("status", sa.String(60), nullable=True),
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("platform", sa.String(30), nullable=False, server_default="unattributed"),
        sa.Column("utm_source", sa.String(120), nullable=True),
        sa.Column("utm_medium", sa.String(120), nullable=True),
        sa.Column("utm_campaign", sa.String(200), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "external_id", name="uq_donation_orders_user_ext"),
        schema="app",
    )
    op.create_index("ix_donation_orders_user_date", "donation_orders",
                    ["user_id", "date"], schema="app")


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("donation_orders", schema="app"):
        op.drop_table("donation_orders", schema="app")
