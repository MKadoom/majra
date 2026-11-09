"""metric semantics (SPEC §5a): WhatsApp message columns, metric coverage, SAR peg

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-03

- norm.fact_ad_performance gains messages_sent / messages_delivered. Existing
  rows written by the WhatsApp connector stored messages sent in `purchases`
  and messages delivered in `impressions`; those are MOVED to the new columns
  (only for facts whose connection platform is 'whatsapp' — Meta accounts
  relabelled 'whatsapp' via platform_override carry real Meta purchases and are
  left alone).
- app.metric_coverage, seeded with TikTok purchases' first day of real data
  (the first date with purchases > 0) — read from the data, not assumed.
- The SAR FX rate is pinned to the peg (1 USD = 3.75 SAR).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

WA_FACTS = """
    account_id IN (
        SELECT a.id FROM norm.accounts a
        JOIN app.connections c ON c.id = a.connection_id
        WHERE c.platform = 'whatsapp')
"""


def upgrade() -> None:
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("fact_ad_performance", schema="norm")}
    fresh = "messages_sent" not in cols
    for name in ("messages_sent", "messages_delivered"):
        if name not in cols:
            op.add_column("fact_ad_performance",
                          sa.Column(name, sa.BigInteger(), nullable=False,
                                    server_default="0"),
                          schema="norm")
    if fresh:
        op.execute(f"""
            UPDATE norm.fact_ad_performance
            SET messages_sent = purchases, messages_delivered = impressions,
                purchases = 0, impressions = 0
            WHERE {WA_FACTS}
        """)

    if not insp.has_table("metric_coverage", schema="app"):
        op.create_table(
            "metric_coverage",
            sa.Column("platform", sa.String(50), primary_key=True),
            sa.Column("metric", sa.String(50), primary_key=True),
            sa.Column("valid_from", sa.Date(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            schema="app",
        )
    op.execute("""
        INSERT INTO app.metric_coverage (platform, metric, valid_from, note)
        SELECT 'tiktok', 'purchases', min(date),
               'first day with TikTok purchase data (complete_payment)'
        FROM norm.fact_ad_performance
        WHERE platform = 'tiktok' AND purchases > 0
        HAVING min(date) IS NOT NULL
        ON CONFLICT (platform, metric) DO NOTHING
    """)

    op.execute("""
        INSERT INTO app.fx_rates (currency, rate_to_usd, updated_at)
        VALUES ('SAR', 1.0 / 3.75, now())
        ON CONFLICT (currency) DO UPDATE SET rate_to_usd = 1.0 / 3.75,
                                             updated_at = now()
    """)


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("metric_coverage", schema="app"):
        op.drop_table("metric_coverage", schema="app")
    cols = {c["name"] for c in insp.get_columns("fact_ad_performance", schema="norm")}
    if "messages_sent" in cols:
        op.execute(f"""
            UPDATE norm.fact_ad_performance
            SET purchases = messages_sent, impressions = messages_delivered
            WHERE {WA_FACTS}
        """)
        op.drop_column("fact_ad_performance", "messages_sent", schema="norm")
        op.drop_column("fact_ad_performance", "messages_delivered", schema="norm")
