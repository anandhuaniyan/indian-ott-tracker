"""Add current content-safety state and immutable transition history.

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
"""

from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("safety_classification", sa.String(length=20), server_default="UNKNOWN", nullable=False))
    op.add_column("movies", sa.Column("safety_risk_score", sa.Integer(), server_default="0", nullable=False))
    op.add_column("movies", sa.Column("safety_reasons", sa.JSON()))
    op.add_column("movies", sa.Column("safety_public_visibility", sa.String(length=20), server_default="FULL", nullable=False))
    op.add_column("movies", sa.Column("safety_classification_source", sa.String(length=20)))
    op.add_column("movies", sa.Column("safety_classified_at", sa.DateTime(timezone=True)))
    op.add_column("movies", sa.Column("safety_manual_override", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_index("ix_movies_safety_classification", "movies", ["safety_classification"])
    op.create_index("ix_movies_safety_public_visibility", "movies", ["safety_public_visibility"])
    op.create_table(
        "content_safety_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("imdb_id", sa.String(length=32)),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("original_title", sa.String(length=500)),
        sa.Column("release_date", sa.Date()),
        sa.Column("language", sa.String(length=10)),
        sa.Column("previous_classification", sa.String(length=20), nullable=False),
        sa.Column("new_classification", sa.String(length=20), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("tmdb_adult", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("certification", sa.String(length=50)),
        sa.Column("reasons", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("classification_source", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("public_visibility_before", sa.String(length=20), nullable=False),
        sa.Column("public_visibility_after", sa.String(length=20), nullable=False),
        sa.Column("notification_status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("notification_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("notification_error", sa.Text()),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("movie_id", "tmdb_id", "imdb_id", "title", "language", "previous_classification", "new_classification", "classification_source", "action", "notification_status"):
        op.create_index(f"ix_content_safety_audits_{column}", "content_safety_audits", [column])


def downgrade() -> None:
    op.drop_table("content_safety_audits")
    op.drop_index("ix_movies_safety_public_visibility", table_name="movies")
    op.drop_index("ix_movies_safety_classification", table_name="movies")
    for column in ("safety_manual_override", "safety_classified_at", "safety_classification_source", "safety_public_visibility", "safety_reasons", "safety_risk_score", "safety_classification"):
        op.drop_column("movies", column)
