"""Persist the administrator override for the daily question limit."""

import sqlalchemy as sa
from alembic import op

revision = "a61cbcbe8c40"
down_revision = "7c821d645a10"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "application_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("daily_model_limit", sa.Integer(), nullable=True),
    )
    op.bulk_insert(table, [{"id": 1, "daily_model_limit": None}])


def downgrade():
    op.drop_table("application_settings")
