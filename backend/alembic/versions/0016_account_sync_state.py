"""per-account sync coverage: app.account_sync_state

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-03

Guarded like 0002-0015 (a no-op create on a fresh DB, where the 0001 bootstrap
already ran create_all). On an existing DB it creates the table and SEEDS it from
the facts already stored: each account's coverage is [min(date), max(date)] per
breakdown, with the backfill marked complete. Seeding keeps the scheduler's
behaviour unchanged on deploy — without it every account would look unsynced and
trigger a full re-backfill (which would hammer Meta's development-tier limit).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not insp.has_table("account_sync_state", schema="app"):
        op.create_table(
            "account_sync_state",
            sa.Column("account_id", sa.BigInteger(),
                      sa.ForeignKey("norm.accounts.id", ondelete="CASCADE"),
                      primary_key=True),
            sa.Column("breakdown", sa.String(length=30), primary_key=True),
            sa.Column("synced_from", sa.Date(), nullable=False),
            sa.Column("synced_to", sa.Date(), nullable=False),
            sa.Column("backfill_completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            schema="app",
        )
    seeds = [
        ("norm.fact_ad_performance", "breakdown"),
        ("norm.fact_web_performance", "'none'"),
        ("norm.fact_social_performance", "'none'"),
    ]
    for table, bd in seeds:
        group = "account_id, breakdown" if bd == "breakdown" else "account_id"
        schema, name = table.split(".")
        if not insp.has_table(name, schema=schema):
            continue
        op.execute(f"""
            INSERT INTO app.account_sync_state
                (account_id, breakdown, synced_from, synced_to,
                 backfill_completed_at, updated_at)
            SELECT account_id, {bd}, min(date), max(date), now(), now()
            FROM {table}
            GROUP BY {group}
            ON CONFLICT (account_id, breakdown) DO NOTHING
        """)


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("account_sync_state", schema="app"):
        op.drop_table("account_sync_state", schema="app")
