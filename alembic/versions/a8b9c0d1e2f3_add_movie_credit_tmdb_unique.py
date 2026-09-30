"""Prevent duplicate TMDB credit re-imports.

Revision ID: a8b9c0d1e2f3
Revises: f7c8d9e0f1a2
Create Date: 2026-09-09

Adds a UNIQUE constraint on movie_credits (movie_id, tmdb_credit_id) so that
a given TMDB credit can only be linked to a movie once. PostgreSQL treats NULL
tmdb_credit_id values as distinct, so legacy/unmapped credits are unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_movie_credit_tmdb_credit",
        "movie_credits",
        ["movie_id", "tmdb_credit_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_movie_credit_tmdb_credit", "movie_credits")