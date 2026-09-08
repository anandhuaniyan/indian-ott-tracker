"""Add trailer health/ranking columns.

Revision ID: f7c8d9e0f1a2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-07

Adds country/size metadata plus is_unavailable/last_checked_at health
tracking to movie_trailers so unavailable videos can be demoted and
re-verified without deleting rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("movie_trailers", sa.Column("country", sa.String(10), nullable=True))
    op.add_column("movie_trailers", sa.Column("size", sa.Integer(), nullable=True))
    op.add_column(
        "movie_trailers",
        sa.Column("is_unavailable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("movie_trailers", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_movie_trailers_is_unavailable", "movie_trailers", ["is_unavailable"])
    op.create_index("ix_movie_trailers_last_checked_at", "movie_trailers", ["last_checked_at"])
    op.create_index("ix_movie_trailers_movie_available", "movie_trailers", ["movie_id", "is_unavailable"])


def downgrade() -> None:
    op.drop_index("ix_movie_trailers_movie_available", table_name="movie_trailers")
    op.drop_index("ix_movie_trailers_last_checked_at", table_name="movie_trailers")
    op.drop_index("ix_movie_trailers_is_unavailable", table_name="movie_trailers")
    op.drop_column("movie_trailers", "last_checked_at")
    op.drop_column("movie_trailers", "is_unavailable")
    op.drop_column("movie_trailers", "size")
    op.drop_column("movie_trailers", "country")