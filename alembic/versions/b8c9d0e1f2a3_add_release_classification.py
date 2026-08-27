"""Add persisted release classification and OTT research eligibility.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("release_status_code", sa.String(32), nullable=True))
    op.add_column("movies", sa.Column("theatrical_release_date", sa.Date(), nullable=True))
    op.add_column("movies", sa.Column("ott_research_eligibility", sa.String(32), nullable=True))
    op.add_column("movies", sa.Column("release_classified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_movies_release_status_code", "movies", ["release_status_code"])
    op.create_index("ix_movies_theatrical_release_date", "movies", ["theatrical_release_date"])
    op.create_index("ix_movies_ott_research_eligibility", "movies", ["ott_research_eligibility"])


def downgrade() -> None:
    op.drop_index("ix_movies_ott_research_eligibility", table_name="movies")
    op.drop_index("ix_movies_theatrical_release_date", table_name="movies")
    op.drop_index("ix_movies_release_status_code", table_name="movies")
    op.drop_column("movies", "release_classified_at")
    op.drop_column("movies", "ott_research_eligibility")
    op.drop_column("movies", "theatrical_release_date")
    op.drop_column("movies", "release_status_code")
