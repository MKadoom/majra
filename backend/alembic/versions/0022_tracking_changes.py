"""Tracking changes: days a metric's meaning changed (SPEC §5a)

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-09

app.tracking_changes, seeded with the 2026-11-09/10 tracking clean-up (each
change was made and confirmed with the owner):
- google_ads: GTM v17 dedupes the Purchase conversion by transaction ID.
- snapchat: the Stape Snapchat gateway was deleted; the Jood store's Snap CAPI
  is the single server sender.
- meta, snapchat, tiktok: Jood's "send pixel events only for ad-link visits"
  (OnlySendPixelEventsForLinkedAffiliate) switched on at about 13:40 Riyadh.
- tiktok, meta: their ad links tagged by 2026-11-10 (time not recorded); the
  row is dated 2026-11-10, the latest day it can have happened.
"""
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

NOV9, NOV10 = date(2026, 11, 9), date(2026, 11, 10)
LINK_ONLY = ("Jood store pixels send Purchase only to the platform of the ad link "
             "(?a=…) from about 13:40 Riyadh; purchases with no ad link go to no "
             "platform")
LINK_TAGGED = ("Jood ad link tagged to this platform between 2026-11-09 13:40 and "
               "2026-11-10 (exact time not recorded); before that its link-driven "
               "purchases were not sent to it")
SEED = [
    (NOV9, "google_ads", "purchases",
     "GTM v17: Purchase conversion deduplicated by transaction ID"),
    (NOV9, "snapchat", "purchases",
     "Stape Snapchat gateway removed: Jood's Snap CAPI is the single server sender; "
     "server-side Purchase before this was about 2x (Nov 4-10: CAPI 1,730 vs "
     "pixel 972; Nov 10 alone 33 vs 30)"),
    (NOV9, "meta", "purchases", LINK_ONLY),
    (NOV9, "snapchat", "purchases", LINK_ONLY),
    (NOV9, "tiktok", "purchases", LINK_ONLY),
    (NOV10, "tiktok", "purchases", LINK_TAGGED),
    (NOV10, "meta", "purchases", LINK_TAGGED),
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
    for d, p, m, n in SEED:
        op.execute(stmt.bindparams(d=d, p=p, m=m, n=n))


def downgrade() -> None:
    op.drop_table("tracking_changes", schema="app")
