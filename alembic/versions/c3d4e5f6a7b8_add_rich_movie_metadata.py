"""Add normalized movie-only metadata foundation.

This migration is additive: it preserves all existing movie IDs, TMDB IDs,
relationships, and both current and legacy OTT tables.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("tagline", sa.Text(), nullable=True))
    op.add_column("movies", sa.Column("budget", sa.BigInteger(), nullable=True))
    op.add_column("movies", sa.Column("revenue", sa.BigInteger(), nullable=True))

    op.create_table("people", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("tmdb_id", sa.Integer()), sa.Column("name", sa.String(255), nullable=False), sa.Column("profile_path", sa.String(500)), sa.Column("known_for_department", sa.String(100)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_index("ix_people_tmdb_id", "people", ["tmdb_id"], unique=True)
    op.create_index("ix_people_name", "people", ["name"])
    op.create_table("movie_credits", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False), sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False), sa.Column("tmdb_credit_id", sa.String(100)), sa.Column("credit_type", sa.String(20), nullable=False), sa.Column("character", sa.String(500)), sa.Column("cast_order", sa.Integer()), sa.Column("department", sa.String(100)), sa.Column("job", sa.String(255)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.UniqueConstraint("movie_id", "person_id", "credit_type", "job", "character", name="uq_movie_credit"))
    op.create_index("ix_movie_credits_movie_id", "movie_credits", ["movie_id"])
    op.create_index("ix_movie_credits_person_id", "movie_credits", ["person_id"])
    op.create_index("ix_movie_credits_credit_type", "movie_credits", ["credit_type"])
    op.create_index("ix_movie_credits_tmdb_credit_id", "movie_credits", ["tmdb_credit_id"])

    op.create_table("keywords", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("tmdb_id", sa.Integer()), sa.Column("name", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.UniqueConstraint("name"))
    op.create_index("ix_keywords_tmdb_id", "keywords", ["tmdb_id"], unique=True)
    op.create_table("movie_keywords", sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True), sa.Column("keyword_id", sa.Integer(), sa.ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True))

    op.create_table("production_companies", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("tmdb_id", sa.Integer()), sa.Column("name", sa.String(255), nullable=False), sa.Column("logo_path", sa.String(500)), sa.Column("origin_country", sa.String(2)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_index("ix_production_companies_tmdb_id", "production_companies", ["tmdb_id"], unique=True)
    op.create_index("ix_production_companies_name", "production_companies", ["name"])
    op.create_table("movie_production_companies", sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True), sa.Column("production_company_id", sa.Integer(), sa.ForeignKey("production_companies.id", ondelete="CASCADE"), primary_key=True))

    op.create_table("production_countries", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("iso_3166_1", sa.String(2), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.UniqueConstraint("iso_3166_1"))
    op.create_index("ix_production_countries_iso_3166_1", "production_countries", ["iso_3166_1"], unique=True)
    op.create_table("movie_production_countries", sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True), sa.Column("production_country_id", sa.Integer(), sa.ForeignKey("production_countries.id", ondelete="CASCADE"), primary_key=True))

    op.create_table("external_ids", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False), sa.Column("provider", sa.String(50), nullable=False), sa.Column("external_id", sa.String(255), nullable=False), sa.Column("source_url", sa.String(1000)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.UniqueConstraint("movie_id", "provider", name="uq_movie_external_id_provider"), sa.UniqueConstraint("provider", "external_id", name="uq_external_id_provider_value"))
    op.create_index("ix_external_ids_movie_id", "external_ids", ["movie_id"])
    op.create_index("ix_external_ids_provider", "external_ids", ["provider"])
    op.create_index("ix_external_ids_external_id", "external_ids", ["external_id"])

    op.create_table("movie_release_dates", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False), sa.Column("country", sa.String(2), nullable=False), sa.Column("release_date", sa.Date(), nullable=False), sa.Column("release_type", sa.String(50), nullable=False), sa.Column("certification", sa.String(50)), sa.Column("note", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.UniqueConstraint("movie_id", "country", "release_date", "release_type", name="uq_movie_release"))
    op.create_index("ix_movie_release_dates_movie_id", "movie_release_dates", ["movie_id"])
    op.create_index("ix_movie_release_dates_country", "movie_release_dates", ["country"])
    op.create_index("ix_movie_release_dates_release_date", "movie_release_dates", ["release_date"])
    op.create_index("ix_movie_release_dates_release_type", "movie_release_dates", ["release_type"])

    op.create_table("movie_images", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False), sa.Column("image_type", sa.String(30), nullable=False), sa.Column("source", sa.String(50), nullable=False), sa.Column("source_id", sa.String(255)), sa.Column("original_url", sa.String(1000)), sa.Column("local_path", sa.String(1000)), sa.Column("language", sa.String(10)), sa.Column("width", sa.Integer()), sa.Column("height", sa.Integer()), sa.Column("aspect_ratio", sa.Float()), sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False), sa.Column("downloaded_at", sa.DateTime(timezone=True)), sa.Column("last_verified_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.UniqueConstraint("movie_id", "image_type", "source", "source_id", name="uq_movie_image_source"))
    op.create_index("ix_movie_images_movie_id", "movie_images", ["movie_id"])
    op.create_index("ix_movie_images_image_type", "movie_images", ["image_type"])
    op.create_index("ix_movie_images_source", "movie_images", ["source"])
    op.create_index("ix_movie_images_local_path", "movie_images", ["local_path"])

    op.create_table("movie_ratings", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="CASCADE"), nullable=False), sa.Column("source", sa.String(50), nullable=False), sa.Column("rating", sa.Float()), sa.Column("vote_count", sa.Integer()), sa.Column("last_updated_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.UniqueConstraint("movie_id", "source", name="uq_movie_rating_source"))
    op.create_index("ix_movie_ratings_movie_id", "movie_ratings", ["movie_id"])
    op.create_index("ix_movie_ratings_source", "movie_ratings", ["source"])


def downgrade() -> None:
    for table, indexes in (("movie_ratings", ["ix_movie_ratings_source", "ix_movie_ratings_movie_id"]), ("movie_images", ["ix_movie_images_local_path", "ix_movie_images_source", "ix_movie_images_image_type", "ix_movie_images_movie_id"]), ("movie_release_dates", ["ix_movie_release_dates_release_type", "ix_movie_release_dates_release_date", "ix_movie_release_dates_country", "ix_movie_release_dates_movie_id"]), ("external_ids", ["ix_external_ids_external_id", "ix_external_ids_provider", "ix_external_ids_movie_id"]), ("production_countries", ["ix_production_countries_iso_3166_1"]), ("production_companies", ["ix_production_companies_name", "ix_production_companies_tmdb_id"]), ("keywords", ["ix_keywords_tmdb_id"]), ("movie_credits", ["ix_movie_credits_tmdb_credit_id", "ix_movie_credits_credit_type", "ix_movie_credits_person_id", "ix_movie_credits_movie_id"]), ("people", ["ix_people_name", "ix_people_tmdb_id"])):
        for index in indexes:
            op.drop_index(index, table_name=table)
    op.drop_table("movie_ratings")
    op.drop_table("movie_images")
    op.drop_table("movie_release_dates")
    op.drop_table("external_ids")
    op.drop_table("movie_production_countries")
    op.drop_table("production_countries")
    op.drop_table("movie_production_companies")
    op.drop_table("production_companies")
    op.drop_table("movie_keywords")
    op.drop_table("keywords")
    op.drop_table("movie_credits")
    op.drop_table("people")
    op.drop_column("movies", "revenue")
    op.drop_column("movies", "budget")
    op.drop_column("movies", "tagline")
