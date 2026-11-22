"""Persist question execution stage for safe recovery."""

import sqlalchemy as sa
from alembic import op

revision = "c43e5f7a2b19"
down_revision = "a61cbcbe8c40"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("questions", sa.Column("processing_stage", sa.String(20), nullable=True))
    op.create_index("ix_questions_queue", "questions", ["status", "processing_stage", "created_at", "id"])


def downgrade():
    op.drop_index("ix_questions_queue", table_name="questions")
    op.drop_column("questions", "processing_stage")
