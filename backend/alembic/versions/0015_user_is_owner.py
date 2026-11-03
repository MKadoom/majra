"""owner flag: app.users.is_owner

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-02

Guarded like 0002-0014: a no-op on a fresh DB (the 0001 bootstrap creates the
current metadata via create_all). On an existing DB it adds the column and marks
the earliest account as the owner, so the operator keeps FX write access.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("users", schema="app")}
    if "is_owner" not in cols:
        op.add_column(
            "users",
            sa.Column("is_owner", sa.Boolean(), nullable=False, server_default="false"),
            schema="app",
        )
    op.execute(
        "UPDATE app.users SET is_owner = true "
        "WHERE id = (SELECT min(id) FROM app.users) "
        "AND NOT EXISTS (SELECT 1 FROM app.users WHERE is_owner)"
    )


def downgrade() -> None:
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("users", schema="app")}
    if "is_owner" in cols:
        op.drop_column("users", "is_owner", schema="app")
