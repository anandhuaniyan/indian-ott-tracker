"""Add verified movie-request snapshots, delivery tracking and SLA state.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from alembic import op
import sqlalchemy as sa


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


EMAIL_KINDS = ("confirmation", "completion", "rejection")


def upgrade():
    op.add_column("movie_requests", sa.Column("verified_title", sa.String(length=500), nullable=True))
    op.add_column("movie_requests", sa.Column("original_title", sa.String(length=500), nullable=True))
    op.add_column("movie_requests", sa.Column("verified_release_date", sa.Date(), nullable=True))
    op.add_column("movie_requests", sa.Column("verified_original_language", sa.String(length=20), nullable=True))
    op.add_column("movie_requests", sa.Column("verified_language_name", sa.String(length=100), nullable=True))
    op.add_column("movie_requests", sa.Column("poster_path", sa.String(length=1000), nullable=True))
    op.add_column("movie_requests", sa.Column("backdrop_path", sa.String(length=1000), nullable=True))
    op.add_column("movie_requests", sa.Column("verified_overview", sa.Text(), nullable=True))
    op.add_column("movie_requests", sa.Column("verified_genres", sa.JSON(), nullable=True))
    op.add_column("movie_requests", sa.Column("verified_status", sa.String(length=100), nullable=True))
    op.add_column("movie_requests", sa.Column("imdb_id", sa.String(length=32), nullable=True))
    op.add_column("movie_requests", sa.Column("director", sa.String(length=500), nullable=True))
    op.add_column("movie_requests", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("movie_requests", sa.Column("local_movie_id", sa.Integer(), nullable=True))
    op.add_column("movie_requests", sa.Column("public_rejection_reason", sa.Text(), nullable=True))
    op.add_column("movie_requests", sa.Column("internal_rejection_reason", sa.Text(), nullable=True))
    for kind in EMAIL_KINDS:
        # Historical rows keep an explicit non-delivery state. New requests use
        # PENDING and are handled by the idempotent delivery service.
        op.add_column(
            "movie_requests",
            sa.Column(f"{kind}_email_status", sa.String(length=20), nullable=False, server_default="NOT_CONFIGURED"),
        )
        op.alter_column(
            "movie_requests",
            f"{kind}_email_status",
            existing_type=sa.String(length=20),
            server_default="PENDING",
        )
        op.add_column("movie_requests", sa.Column(f"{kind}_email_sent_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("movie_requests", sa.Column(f"{kind}_email_last_error", sa.Text(), nullable=True))
        op.add_column("movie_requests", sa.Column(f"{kind}_email_last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("movie_requests", sa.Column("sla_36_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("movie_requests", sa.Column("sla_48_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_movie_requests_local_movie_id_movies",
        "movie_requests",
        "movies",
        ["local_movie_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_movie_requests_local_movie_id", "movie_requests", ["local_movie_id"])
    op.create_index("ix_movie_requests_imdb_id", "movie_requests", ["imdb_id"])
    op.create_index("ix_movie_requests_status_created_at", "movie_requests", ["status", "created_at"])
    op.drop_index("uq_movie_requests_active_external_id", table_name="movie_requests")
    op.create_index(
        "uq_movie_requests_active_external_id",
        "movie_requests",
        ["external_movie_id"],
        unique=True,
        postgresql_where=sa.text(
            "external_movie_id IS NOT NULL AND status IN ('PENDING', 'REVIEWING', 'FOUND')"
        ),
    )


def downgrade():
    op.drop_index("uq_movie_requests_active_external_id", table_name="movie_requests")
    op.create_index(
        "uq_movie_requests_active_external_id",
        "movie_requests",
        ["external_movie_id"],
        unique=True,
        postgresql_where=sa.text(
            "external_movie_id IS NOT NULL AND status IN ('PENDING', 'REVIEWING')"
        ),
    )
    op.drop_index("ix_movie_requests_status_created_at", table_name="movie_requests")
    op.drop_index("ix_movie_requests_imdb_id", table_name="movie_requests")
    op.drop_index("ix_movie_requests_local_movie_id", table_name="movie_requests")
    op.drop_constraint("fk_movie_requests_local_movie_id_movies", "movie_requests", type_="foreignkey")
    op.drop_column("movie_requests", "sla_48_notified_at")
    op.drop_column("movie_requests", "sla_36_notified_at")
    for kind in reversed(EMAIL_KINDS):
        op.drop_column("movie_requests", f"{kind}_email_last_attempt_at")
        op.drop_column("movie_requests", f"{kind}_email_last_error")
        op.drop_column("movie_requests", f"{kind}_email_sent_at")
        op.drop_column("movie_requests", f"{kind}_email_status")
    for column in (
        "internal_rejection_reason", "public_rejection_reason", "local_movie_id", "verified_at",
        "director", "imdb_id", "verified_status", "verified_genres", "verified_overview",
        "backdrop_path", "poster_path", "verified_language_name", "verified_original_language",
        "verified_release_date", "original_title", "verified_title",
    ):
        op.drop_column("movie_requests", column)
