"""Add user accounts and weekly report mail tracking."""
from alembic import op
import sqlalchemy as sa

revision = "ed47f023da21"
down_revision = "526a2925a370"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_accounts",
        sa.Column("registration_id", sa.String(32), sa.ForeignKey("registrations.id"), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
    )
    with op.batch_alter_table("mail_jobs") as batch:
        batch.alter_column("event_id", existing_type=sa.String(32), nullable=True)
        batch.add_column(sa.Column("week_start", sa.String(10)))
        batch.create_unique_constraint("uq_mail_jobs_week_start", ["week_start"])
    op.create_table(
        "weekly_mail_items",
        sa.Column("issue_id", sa.String(32), sa.ForeignKey("issues.id"), primary_key=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("mail_jobs.id"), nullable=False),
    )
    op.create_index("ix_weekly_mail_items_job_id", "weekly_mail_items", ["job_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM mail_jobs WHERE week_start IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Export weekly mail records before rolling back this migration.")
    op.drop_table("weekly_mail_items")
    with op.batch_alter_table("mail_jobs") as batch:
        batch.drop_constraint("uq_mail_jobs_week_start", type_="unique")
        batch.drop_column("week_start")
        batch.alter_column("event_id", existing_type=sa.String(32), nullable=False)
    op.drop_table("user_accounts")
