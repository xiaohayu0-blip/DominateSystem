"""Store feedback for individual answer items."""

import sqlalchemy as sa
from alembic import op

revision = "e81c2b6a7d30"
down_revision = "c43e5f7a2b19"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "question_feedback",
        sa.Column("question_id", sa.String(32), sa.ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("item_index", sa.Integer(), primary_key=True),
        sa.Column("rating", sa.String(8), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("question_feedback")
