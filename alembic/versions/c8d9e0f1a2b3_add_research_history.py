"""Add research provenance and request communication history.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""

from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("parent_run_id", sa.String(36)),
        sa.Column("trigger_type", sa.String(40), nullable=False),
        sa.Column("initiated_by", sa.String(100), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), server_default="QUEUED", nullable=False),
        sa.Column("result", sa.String(30)),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("movies.id", ondelete="SET NULL")),
        sa.Column("request_id", sa.String(32)),
        sa.Column("active_key", sa.String(120), unique=True),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("queries_attempted", sa.JSON()),
        sa.Column("providers_attempted", sa.JSON()),
        sa.Column("web_searches_attempted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sources_discovered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("evidence_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("before_platform", sa.String(100)),
        sa.Column("after_platform", sa.String(100)),
        sa.Column("before_release_date", sa.Date()),
        sa.Column("after_release_date", sa.Date()),
        sa.Column("before_imdb_rating", sa.Float()),
        sa.Column("after_imdb_rating", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("database_changes", sa.JSON()),
        sa.Column("notification_results", sa.JSON()),
        sa.Column("errors", sa.JSON()),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    for column in ("run_id", "parent_run_id", "trigger_type", "initiated_by", "category", "status", "result", "movie_id", "request_id", "started_at", "completed_at"):
        op.create_index(f"ix_research_runs_{column}", "research_runs", [column])

    op.create_table(
        "request_communications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movie_request_id", sa.Integer(), sa.ForeignKey("movie_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("subject", sa.String(500)),
        sa.Column("fingerprint", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("movie_request_id", "event_type", "channel", name="uq_request_communication_event"),
    )
    for column in ("movie_request_id", "event_type", "channel", "status", "fingerprint"):
        op.create_index(f"ix_request_communications_{column}", "request_communications", [column])

    op.add_column("ott_evidence", sa.Column("research_run_id", sa.String(36)))
    op.create_index("ix_ott_evidence_research_run_id", "ott_evidence", ["research_run_id"])
    op.add_column("movie_discovery_runs", sa.Column("trigger_type", sa.String(40), server_default="AUTOMATED_SCHEDULE", nullable=False))
    op.add_column("movie_discovery_runs", sa.Column("initiated_by", sa.String(100), server_default="celery:beat", nullable=False))
    op.add_column("movie_discovery_runs", sa.Column("research_run_id", sa.String(36)))
    op.create_index("ix_movie_discovery_runs_trigger_type", "movie_discovery_runs", ["trigger_type"])
    op.create_index("ix_movie_discovery_runs_initiated_by", "movie_discovery_runs", ["initiated_by"])
    op.create_index("ix_movie_discovery_runs_research_run_id", "movie_discovery_runs", ["research_run_id"])


def downgrade() -> None:
    op.drop_index("ix_movie_discovery_runs_research_run_id", table_name="movie_discovery_runs")
    op.drop_index("ix_movie_discovery_runs_initiated_by", table_name="movie_discovery_runs")
    op.drop_index("ix_movie_discovery_runs_trigger_type", table_name="movie_discovery_runs")
    op.drop_column("movie_discovery_runs", "research_run_id")
    op.drop_column("movie_discovery_runs", "initiated_by")
    op.drop_column("movie_discovery_runs", "trigger_type")
    op.drop_index("ix_ott_evidence_research_run_id", table_name="ott_evidence")
    op.drop_column("ott_evidence", "research_run_id")
    op.drop_table("request_communications")
    op.drop_table("research_runs")
