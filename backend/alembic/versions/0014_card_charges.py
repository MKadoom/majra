"""credit-card top-up ledger: app.card_charges

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-29

Guarded like 0002-0013: a no-op on a fresh DB (the 0001 bootstrap creates the
current metadata via create_all), creates the table on a DB migrated before it
existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not insp.has_table("card_charges", schema="app"):
        op.create_table(
            "card_charges",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("amount", sa.Numeric(18, 2), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=True),
            sa.Column("note", sa.String(length=300), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["app.users.id"]),
            schema="app",
        )
        op.create_index(
            "ix_app_card_charges_user_id", "card_charges", ["user_id"], schema="app"
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("card_charges", schema="app"):
        op.drop_index(
            "ix_app_card_charges_user_id", table_name="card_charges", schema="app"
        )
        op.drop_table("card_charges", schema="app")
