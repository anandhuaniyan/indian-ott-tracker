"""Add whole-database health tracking and query indexes.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("collection_tmdb_id", sa.Integer(), nullable=True))
    op.add_column("movies", sa.Column("collection_name", sa.String(500), nullable=True))
    op.add_column("movies", sa.Column("collection_poster_path", sa.String(500), nullable=True))
    op.add_column("movies", sa.Column("collection_backdrop_path", sa.String(500), nullable=True))
    op.create_index("ix_movies_collection_tmdb_id", "movies", ["collection_tmdb_id"])
    op.create_table("alternative_titles", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False), sa.Column("country", sa.String(2), nullable=True), sa.Column("title", sa.String(500), nullable=False), sa.Column("title_type", sa.String(100), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.UniqueConstraint("movie_id", "country", "title", name="uq_movie_alternative_title"))
    op.create_index("ix_alternative_titles_movie_id", "alternative_titles", ["movie_id"])
    op.create_index("ix_alternative_titles_country", "alternative_titles", ["country"])
    op.create_index("ix_alternative_titles_title", "alternative_titles", ["title"])
    op.add_column("data_quality_issues", sa.Column("person_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_data_quality_issues_person", "data_quality_issues", "people", ["person_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_data_quality_issues_person_id", "data_quality_issues", ["person_id"])
    op.add_column("operation_states", sa.Column("processed_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("operation_states", sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_movies_original_language_release", "movies", ["original_language", "release_date"])
    op.create_index("ix_movies_popularity", "movies", ["popularity"])
    op.create_index("ix_movies_vote_average", "movies", ["vote_average"])
    op.create_index("ix_movies_created_at", "movies", ["created_at"])
    op.create_index("ix_movie_credits_movie_role", "movie_credits", ["movie_id", "credit_type", "job"])
    op.create_index("ix_ott_availability_provider_date", "ott_availability", ["provider", "ott_release_date"])
    op.create_index("ix_ott_availability_status_date", "ott_availability", ["status", "ott_release_date"])
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_movies_title_trgm ON movies USING gin (lower(title) gin_trgm_ops)")
    op.execute("CREATE INDEX ix_movies_original_title_trgm ON movies USING gin (lower(original_title) gin_trgm_ops)")
    op.execute("CREATE INDEX ix_people_name_trgm ON people USING gin (lower(name) gin_trgm_ops)")
    op.execute("CREATE INDEX ix_keywords_name_trgm ON keywords USING gin (lower(name) gin_trgm_ops)")
    op.execute("CREATE INDEX ix_alternative_titles_title_trgm ON alternative_titles USING gin (lower(title) gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_alternative_titles_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_keywords_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_people_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_movies_original_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_movies_title_trgm")
    op.drop_index("ix_ott_availability_status_date", table_name="ott_availability")
    op.drop_index("ix_ott_availability_provider_date", table_name="ott_availability")
    op.drop_index("ix_movie_credits_movie_role", table_name="movie_credits")
    op.drop_index("ix_movies_created_at", table_name="movies")
    op.drop_index("ix_movies_vote_average", table_name="movies")
    op.drop_index("ix_movies_popularity", table_name="movies")
    op.drop_index("ix_movies_original_language_release", table_name="movies")
    op.drop_column("operation_states", "last_failure_at")
    op.drop_column("operation_states", "processed_count")
    op.drop_index("ix_data_quality_issues_person_id", table_name="data_quality_issues")
    op.drop_constraint("fk_data_quality_issues_person", "data_quality_issues", type_="foreignkey")
    op.drop_column("data_quality_issues", "person_id")
    op.drop_index("ix_alternative_titles_title", table_name="alternative_titles")
    op.drop_index("ix_alternative_titles_country", table_name="alternative_titles")
    op.drop_index("ix_alternative_titles_movie_id", table_name="alternative_titles")
    op.drop_table("alternative_titles")
    op.drop_index("ix_movies_collection_tmdb_id", table_name="movies")
    op.drop_column("movies", "collection_backdrop_path")
    op.drop_column("movies", "collection_poster_path")
    op.drop_column("movies", "collection_name")
    op.drop_column("movies", "collection_tmdb_id")
