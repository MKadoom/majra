"""spend provenance: norm.fact_ad_performance.cost_source (T6)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-03

Adds `cost_source` (default 'platform'). Every row the WhatsApp connector wrote
before T6 was priced as delivered × a configured rate, so those are marked
'estimate'. The next sync of each range replaces them with Meta's
pricing_analytics cost where Meta returns one.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("fact_ad_performance", schema="norm")}
    if "cost_source" not in cols:
        op.add_column("fact_ad_performance",
                      sa.Column("cost_source", sa.String(30), nullable=False,
                                server_default="platform"),
                      schema="norm")
        op.execute("""
            UPDATE norm.fact_ad_performance SET cost_source = 'estimate'
            WHERE account_id IN (
                SELECT a.id FROM norm.accounts a
                JOIN app.connections c ON c.id = a.connection_id
                WHERE c.platform = 'whatsapp')
        """)


def downgrade() -> None:
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("fact_ad_performance", schema="norm")}
    if "cost_source" in cols:
        op.drop_column("fact_ad_performance", "cost_source", schema="norm")
