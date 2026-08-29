"""add IMDb rating lifecycle and retry scheduling

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa


revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "movie_ratings",
        sa.Column("status", sa.String(length=32), server_default="PENDING", nullable=False),
    )
    op.add_column(
        "movie_ratings",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("movie_ratings", sa.Column("last_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("movie_ratings", sa.Column("next_check_at", sa.DateTime(timezone=True)))
    op.add_column("movie_ratings", sa.Column("last_error", sa.Text()))
    op.execute(
        """
        UPDATE movie_ratings
        SET status = CASE WHEN rating IS NULL THEN 'NOT_YET_RATED' ELSE 'AVAILABLE' END,
            last_attempt_at = COALESCE(last_updated_at, updated_at)
        WHERE lower(source) = 'imdb'
        """
    )
    op.create_index("ix_movie_ratings_status", "movie_ratings", ["status"])
    op.create_index("ix_movie_ratings_next_check_at", "movie_ratings", ["next_check_at"])
    op.create_index(
        "ix_movie_ratings_source_status_due",
        "movie_ratings",
        ["source", "status", "next_check_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_movie_ratings_source_status_due", table_name="movie_ratings")
    op.drop_index("ix_movie_ratings_next_check_at", table_name="movie_ratings")
    op.drop_index("ix_movie_ratings_status", table_name="movie_ratings")
    op.drop_column("movie_ratings", "last_error")
    op.drop_column("movie_ratings", "next_check_at")
    op.drop_column("movie_ratings", "last_attempt_at")
    op.drop_column("movie_ratings", "attempt_count")
    op.drop_column("movie_ratings", "status")
