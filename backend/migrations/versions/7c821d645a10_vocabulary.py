"""Versioned vocabulary, publication epoch and narrowly scoped student editors."""
from alembic import op
import sqlalchemy as sa

revision = '7c821d645a10'
down_revision = '3f7d99301244'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vocabulary_entries',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('draft', sa.JSON(), nullable=False),
        sa.Column('published', sa.JSON(none_as_null=True)),
        sa.Column('versions', sa.JSON(), nullable=False),
        sa.Column('history', sa.JSON(), nullable=False))
    op.create_table('vocabulary_epoch', sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('revision', sa.Integer(), nullable=False))
    op.execute('INSERT INTO vocabulary_epoch (id, revision) VALUES (1, 0)')
    op.create_table('vocabulary_members', sa.Column('registration_id', sa.String(32),
                    sa.ForeignKey('registrations.id'), primary_key=True))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM vocabulary_entries LIMIT 1')).first():
        raise RuntimeError('Export vocabulary history before removing its tables.')
    op.drop_table('vocabulary_members')
    op.drop_table('vocabulary_epoch')
    op.drop_table('vocabulary_entries')
