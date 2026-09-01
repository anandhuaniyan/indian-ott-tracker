"""Add persistent twice-daily movie discovery checkpoints.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "movie_discovery_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_type", sa.String(20), nullable=False),
        sa.Column("slot", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="RUNNING", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("candidates_discovered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("already_existing", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new_movies_imported", sa.Integer(), server_default="0", nullable=False),
        sa.Column("needs_review", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("language_stats", sa.JSON()),
        sa.Column("source_stats", sa.JSON()),
        sa.Column("last_error", sa.Text()),
        *_timestamps(),
    )
    for column in ("run_type", "slot", "status", "started_at"):
        op.create_index(f"ix_movie_discovery_runs_{column}", "movie_discovery_runs", [column])

    op.create_table(
        "movie_discovery_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("latest_run_id", sa.Integer(), sa.ForeignKey("movie_discovery_runs.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("external_key", sa.String(180), nullable=False),
        sa.Column("tmdb_id", sa.Integer()),
        sa.Column("imdb_id", sa.String(32)),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("original_title", sa.String(500)),
        sa.Column("language", sa.String(20)),
        sa.Column("release_date", sa.Date()),
        sa.Column("status", sa.String(24), server_default="DISCOVERED", nullable=False),
        sa.Column("matched_movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="SET NULL")),
        sa.Column("match_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("match_reason", sa.String(500)),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text()),
        *_timestamps(),
        sa.UniqueConstraint("source", "external_key", name="uq_movie_discovery_candidate_source_key"),
    )
    for column in ("latest_run_id", "source", "tmdb_id", "imdb_id", "title", "language", "release_date", "status", "matched_movie_id", "last_seen_at"):
        op.create_index(f"ix_movie_discovery_candidates_{column}", "movie_discovery_candidates", [column])


def downgrade() -> None:
    op.drop_table("movie_discovery_candidates")
    op.drop_table("movie_discovery_runs")
