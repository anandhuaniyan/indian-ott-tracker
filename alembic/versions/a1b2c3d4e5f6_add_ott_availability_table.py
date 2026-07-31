"""Add ott_availability table

Revision ID: a1b2c3d4e5f6
Revises: 932a0a90eb34
Create Date: 2026-07-31 03:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '932a0a90eb34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ott_availability',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('movie_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=100), nullable=False),
        sa.Column('provider_logo', sa.String(length=500), nullable=True),
        sa.Column('country', sa.String(length=10), server_default='IN', nullable=False),
        sa.Column('watch_type', sa.String(length=50), server_default='subscription', nullable=False),
        sa.Column('ott_release_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=50), server_default='available', nullable=False),
        sa.Column('source_type', sa.String(length=50), server_default='tmdb', nullable=False),
        sa.Column('source_url', sa.String(length=1000), nullable=True),
        sa.Column('confidence', sa.Float(), server_default='100.0', nullable=False),
        sa.Column('last_checked', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['movie_id'], ['movies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('movie_id', 'provider', 'country', 'watch_type', name='uq_movie_ott_provider_country_type')
    )
    op.create_index(op.f('ix_ott_availability_movie_id'), 'ott_availability', ['movie_id'], unique=False)
    op.create_index(op.f('ix_ott_availability_provider'), 'ott_availability', ['provider'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ott_availability_provider'), table_name='ott_availability')
    op.drop_index(op.f('ix_ott_availability_movie_id'), table_name='ott_availability')
    op.drop_table('ott_availability')
