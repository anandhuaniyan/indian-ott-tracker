"""Persistent, secret-free operational history for AI OTT research."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class AIResearchAuditRun(TimestampMixin, Base):
    __tablename__ = "ai_research_audit_runs"
    __table_args__ = (
        Index("ix_ai_research_runs_started_status", "started_at", "status"),
        Index("ix_ai_research_runs_provider_error", "provider", "provider_failures"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    initiated_by: Mapped[str | None] = mapped_column(String(120))
    mode: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(100), index=True)
    model: Mapped[str | None] = mapped_column(String(200), index=True)
    movies_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    movies_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    needs_review: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflicts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    no_reliable_result: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sources_discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sources_inspected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    database_updates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fallback_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    error_summary: Mapped[str | None] = mapped_column(String(1000))


class AIResearchMovieActivity(TimestampMixin, Base):
    __tablename__ = "ai_research_movie_activities"
    __table_args__ = (
        Index("ix_ai_research_movie_run_status", "run_id", "status"),
        Index("ix_ai_research_movie_identity", "movie_id", "tmdb_id", "imdb_id"),
        Index("ix_ai_research_movie_decision", "decision", "completed_at"),
        Index("ix_ai_research_movie_provider_error", "provider", "error_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("ai_research_audit_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    movie_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    original_title: Mapped[str | None] = mapped_column(String(500))
    release_year: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(16), index=True)
    production_countries: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(100), index=True)
    model: Mapped[str | None] = mapped_column(String(200), index=True)
    queries: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    sources_discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sources_inspected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    platforms_found: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    dates_found: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    regions_found: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    watch_types_found: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    identity_confidence: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    platform_confidence: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    date_confidence: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    decision: Mapped[str | None] = mapped_column(String(64), index=True)
    error_type: Mapped[str | None] = mapped_column(String(80), index=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    database_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    before_value: Mapped[dict | None] = mapped_column(JSON)
    proposed_value: Mapped[dict | None] = mapped_column(JSON)
    after_value: Mapped[dict | None] = mapped_column(JSON)
    duration_seconds: Mapped[float | None] = mapped_column(Float)


class AIResearchEvent(Base):
    __tablename__ = "ai_research_events"
    __table_args__ = (
        Index("ix_ai_research_events_run_time", "run_id", "occurred_at"),
        Index("ix_ai_research_events_movie_sequence", "movie_activity_id", "sequence"),
        Index("ix_ai_research_events_type_time", "event_type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("ai_research_audit_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    movie_activity_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_research_movie_activities.id", ondelete="CASCADE"), index=True
    )
    movie_id: Mapped[int | None] = mapped_column(Integer, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    message: Mapped[str | None] = mapped_column(String(1000))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class AIResearchSource(Base):
    __tablename__ = "ai_research_sources"
    __table_args__ = (
        Index("ix_ai_research_sources_run_movie", "run_id", "movie_activity_id"),
        Index("ix_ai_research_sources_domain_status", "domain", "accepted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("ai_research_audit_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    movie_activity_id: Mapped[int] = mapped_column(
        ForeignKey("ai_research_movie_activities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movie_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    page_title: Mapped[str | None] = mapped_column(String(500))
    source_tier: Mapped[str | None] = mapped_column(String(80), index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetch_status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    identity_match: Mapped[str | None] = mapped_column(String(50), index=True)
    platform_evidence: Mapped[str | None] = mapped_column(String(300))
    date_evidence: Mapped[date | None] = mapped_column(Date)
    region_evidence: Mapped[str | None] = mapped_column(String(32))
    watch_type_evidence: Mapped[str | None] = mapped_column(String(64))
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(1000))
    evidence_excerpt: Mapped[str | None] = mapped_column(Text)
