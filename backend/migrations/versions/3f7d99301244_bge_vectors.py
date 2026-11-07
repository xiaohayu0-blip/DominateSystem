"""Store BGE embeddings alongside the existing character vectors."""
from alembic import op
import sqlalchemy as sa

revision = "3f7d99301244"
down_revision = "ed47f023da21"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chunks", sa.Column("bge_vector", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    op.drop_column("chunks", "bge_vector")
