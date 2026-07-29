"""saved Explore views: app.saved_views

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-29

Guarded like 0002-0011: a no-op on a fresh DB (the 0001 bootstrap creates the
current metadata via create_all), creates the table on a DB migrated before it
existed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not insp.has_table("saved_views", schema="app"):
        op.create_table(
            "saved_views",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("config", postgresql.JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["app.users.id"]),
            schema="app",
        )
        op.create_index(
            "ix_app_saved_views_user_id", "saved_views", ["user_id"], schema="app"
        )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("saved_views", schema="app"):
        op.drop_index("ix_app_saved_views_user_id", table_name="saved_views", schema="app")
        op.drop_table("saved_views", schema="app")
