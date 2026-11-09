"""Tracking changes: days a metric's meaning changed (SPEC §5a)

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-09

app.tracking_changes, seeded with the 2026-11-09 tracking clean-up (each change
was made and confirmed with the owner that day):
- google_ads: GTM v17 dedupes the Purchase conversion by transaction ID.
- snapchat: the Stape Snapchat gateway was deleted; the Jood store's Snap CAPI
  is the single server sender.
- meta, snapchat, tiktok: Jood's "send pixel events only for ad-link visits"
  (OnlySendPixelEventsForLinkedAffiliate) switched on at about 13:40 Riyadh.
"""
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

LINK_ONLY = ("Jood store pixels send events only for visits from an ad link "
             "(?a=…) from about 13:40 Riyadh; untagged ad traffic no longer "
             "reports purchases to this platform")
CHANGED_ON = date(2026, 11, 9)
SEED = [
    ("google_ads", "purchases",
     "GTM v17: Purchase conversion deduplicated by transaction ID"),
    ("snapchat", "purchases",
     "Stape Snapchat gateway removed: Jood's Snap CAPI is the single server sender"),
    ("meta", "purchases", LINK_ONLY),
    ("snapchat", "purchases", LINK_ONLY),
    ("tiktok", "purchases", LINK_ONLY),
]


def upgrade() -> None:
    # 0001 builds tables from the current models, so on a fresh database the
    # table already exists; the seed below runs either way and is idempotent.
    if not inspect(op.get_bind()).has_table("tracking_changes", schema="app"):
        op.create_table(
            "tracking_changes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("changed_on", sa.Date(), nullable=False, index=True),
            sa.Column("platform", sa.String(50), nullable=True),
            sa.Column("metric", sa.String(50), nullable=False,
                      server_default="purchases"),
            sa.Column("note", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
            schema="app",
        )
    stmt = sa.text("""
        INSERT INTO app.tracking_changes (changed_on, platform, metric, note, created_at)
        SELECT :d, :p, :m, :n, now()
        WHERE NOT EXISTS (
            SELECT 1 FROM app.tracking_changes
            WHERE changed_on = :d AND platform = :p AND metric = :m AND note = :n)
    """)
    for p, m, n in SEED:
        op.execute(stmt.bindparams(d=CHANGED_ON, p=p, m=m, n=n))


def downgrade() -> None:
    op.drop_table("tracking_changes", schema="app")
