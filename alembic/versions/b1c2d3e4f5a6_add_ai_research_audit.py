"""Add persistent AI research activity and source audit tables.

Revision ID: b1c2d3e4f5a6
Revises: a4c5d6e7f8a9
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "b1c2d3e4f5a6"
down_revision = "a4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_research_audit_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("initiated_by", sa.String(120)),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("provider", sa.String(100)),
        sa.Column("model", sa.String(200)),
        sa.Column("movies_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("movies_processed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("successful", sa.Integer(), server_default="0", nullable=False),
        sa.Column("needs_review", sa.Integer(), server_default="0", nullable=False),
        sa.Column("conflicts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("no_reliable_result", sa.Integer(), server_default="0", nullable=False),
        sa.Column("provider_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sources_discovered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sources_inspected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("database_updates", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fallback_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("error_summary", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for name, columns in (
        ("ix_ai_research_runs_started_at", ["started_at"]),
        ("ix_ai_research_runs_completed_at", ["completed_at"]),
        ("ix_ai_research_runs_status", ["status"]),
        ("ix_ai_research_runs_trigger", ["trigger"]),
        ("ix_ai_research_runs_mode", ["mode"]),
        ("ix_ai_research_runs_provider", ["provider"]),
        ("ix_ai_research_runs_model", ["model"]),
        ("ix_ai_research_runs_started_status", ["started_at", "status"]),
        ("ix_ai_research_runs_provider_error", ["provider", "provider_failures"]),
    ):
        op.create_index(name, "ai_research_audit_runs", columns)

    op.create_table(
        "ai_research_movie_activities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("ai_research_audit_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("tmdb_id", sa.Integer()),
        sa.Column("imdb_id", sa.String(32)),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("original_title", sa.String(500)),
        sa.Column("release_year", sa.Integer()),
        sa.Column("language", sa.String(16)),
        sa.Column("production_countries", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(100)),
        sa.Column("model", sa.String(200)),
        sa.Column("queries", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("sources_discovered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sources_inspected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("platforms_found", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("dates_found", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("regions_found", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("watch_types_found", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("identity_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("platform_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("date_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("decision", sa.String(64)),
        sa.Column("error_type", sa.String(80)),
        sa.Column("error_summary", sa.String(1000)),
        sa.Column("dry_run", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("database_changed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("before_value", sa.JSON()),
        sa.Column("proposed_value", sa.JSON()),
        sa.Column("after_value", sa.JSON()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    indexes = {
        "ix_ai_research_movie_activities_run_id": ["run_id"],
        "ix_ai_research_movie_activities_movie_id": ["movie_id"],
        "ix_ai_research_movie_activities_tmdb_id": ["tmdb_id"],
        "ix_ai_research_movie_activities_imdb_id": ["imdb_id"],
        "ix_ai_research_movie_activities_title": ["title"],
        "ix_ai_research_movie_activities_language": ["language"],
        "ix_ai_research_movie_activities_started_at": ["started_at"],
        "ix_ai_research_movie_activities_completed_at": ["completed_at"],
        "ix_ai_research_movie_activities_status": ["status"],
        "ix_ai_research_movie_activities_provider": ["provider"],
        "ix_ai_research_movie_activities_model": ["model"],
        "ix_ai_research_movie_activities_decision": ["decision"],
        "ix_ai_research_movie_activities_error_type": ["error_type"],
        "ix_ai_research_movie_activities_dry_run": ["dry_run"],
        "ix_ai_research_movie_activities_database_changed": ["database_changed"],
        "ix_ai_research_movie_run_status": ["run_id", "status"],
        "ix_ai_research_movie_identity": ["movie_id", "tmdb_id", "imdb_id"],
        "ix_ai_research_movie_decision": ["decision", "completed_at"],
        "ix_ai_research_movie_provider_error": ["provider", "error_type"],
    }
    for name, columns in indexes.items():
        op.create_index(name, "ai_research_movie_activities", columns)

    op.create_table(
        "ai_research_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("ai_research_audit_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("movie_activity_id", sa.Integer(), sa.ForeignKey("ai_research_movie_activities.id", ondelete="CASCADE")),
        sa.Column("movie_id", sa.Integer()),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("message", sa.String(1000)),
        sa.Column("metadata_json", sa.JSON()),
    )
    for name, columns in (
        ("ix_ai_research_events_run_id", ["run_id"]),
        ("ix_ai_research_events_movie_activity_id", ["movie_activity_id"]),
        ("ix_ai_research_events_movie_id", ["movie_id"]),
        ("ix_ai_research_events_occurred_at", ["occurred_at"]),
        ("ix_ai_research_events_event_type", ["event_type"]),
        ("ix_ai_research_events_run_time", ["run_id", "occurred_at"]),
        ("ix_ai_research_events_movie_sequence", ["movie_activity_id", "sequence"]),
        ("ix_ai_research_events_type_time", ["event_type", "occurred_at"]),
    ):
        op.create_index(name, "ai_research_events", columns)

    op.create_table(
        "ai_research_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("ai_research_audit_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("movie_activity_id", sa.Integer(), sa.ForeignKey("ai_research_movie_activities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("page_title", sa.String(500)),
        sa.Column("source_tier", sa.String(80)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("fetch_status", sa.String(50), nullable=False),
        sa.Column("identity_match", sa.String(50)),
        sa.Column("platform_evidence", sa.String(300)),
        sa.Column("date_evidence", sa.Date()),
        sa.Column("region_evidence", sa.String(32)),
        sa.Column("watch_type_evidence", sa.String(64)),
        sa.Column("accepted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("rejection_reason", sa.String(1000)),
        sa.Column("evidence_excerpt", sa.Text()),
    )
    for name, columns in (
        ("ix_ai_research_sources_run_id", ["run_id"]),
        ("ix_ai_research_sources_movie_activity_id", ["movie_activity_id"]),
        ("ix_ai_research_sources_movie_id", ["movie_id"]),
        ("ix_ai_research_sources_domain", ["domain"]),
        ("ix_ai_research_sources_source_tier", ["source_tier"]),
        ("ix_ai_research_sources_fetch_status", ["fetch_status"]),
        ("ix_ai_research_sources_identity_match", ["identity_match"]),
        ("ix_ai_research_sources_accepted", ["accepted"]),
        ("ix_ai_research_sources_run_movie", ["run_id", "movie_activity_id"]),
        ("ix_ai_research_sources_domain_status", ["domain", "accepted"]),
    ):
        op.create_index(name, "ai_research_sources", columns)


def downgrade() -> None:
    op.drop_table("ai_research_sources")
    op.drop_table("ai_research_events")
    op.drop_table("ai_research_movie_activities")
    op.drop_table("ai_research_audit_runs")
