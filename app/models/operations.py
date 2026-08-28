"""Additive operational models for requests, evidence, health and notifications."""
from datetime import date, datetime
from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database.base import Base
from app.models.mixins import TimestampMixin


class MovieRequest(TimestampMixin, Base):
    __tablename__ = "movie_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    movie_name: Mapped[str] = mapped_column(String(500), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    external_movie_id: Mapped[int | None] = mapped_column(Integer, index=True)
    release_year: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(20))
    details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    verified_title: Mapped[str | None] = mapped_column(String(500))
    original_title: Mapped[str | None] = mapped_column(String(500))
    verified_release_date: Mapped[date | None] = mapped_column(Date)
    verified_original_language: Mapped[str | None] = mapped_column(String(20))
    verified_language_name: Mapped[str | None] = mapped_column(String(100))
    poster_path: Mapped[str | None] = mapped_column(String(1000))
    backdrop_path: Mapped[str | None] = mapped_column(String(1000))
    verified_overview: Mapped[str | None] = mapped_column(Text)
    verified_genres: Mapped[list | None] = mapped_column(JSON)
    verified_status: Mapped[str | None] = mapped_column(String(100))
    imdb_id: Mapped[str | None] = mapped_column(String(32), index=True)
    director: Mapped[str | None] = mapped_column(String(500))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_movie_id: Mapped[int | None] = mapped_column(ForeignKey("movies.id", ondelete="SET NULL"), index=True)
    public_rejection_reason: Mapped[str | None] = mapped_column(Text)
    internal_rejection_reason: Mapped[str | None] = mapped_column(Text)
    confirmation_email_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    confirmation_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_email_last_error: Mapped[str | None] = mapped_column(Text)
    confirmation_email_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_email_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    completion_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_email_last_error: Mapped[str | None] = mapped_column(Text)
    completion_email_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_email_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    rejection_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_email_last_error: Mapped[str | None] = mapped_column(Text)
    rejection_email_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_36_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_48_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OttEvidence(TimestampMixin, Base):
    __tablename__ = "ott_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="UNKNOWN", index=True)
    platform: Mapped[str | None] = mapped_column(String(100), index=True)
    release_date: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_title: Mapped[str | None] = mapped_column(String(500))
    source_published_at: Mapped[date | None] = mapped_column(Date)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    notes: Mapped[str | None] = mapped_column(Text)


class DataQualityIssue(TimestampMixin, Base):
    __tablename__ = "data_quality_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int | None] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    issue_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    detail: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationLog(TimestampMixin, Base):
    __tablename__ = "notification_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationState(TimestampMixin, Base):
    """Persistent cursors and last-run facts for resumable scheduled work."""
    __tablename__ = "operation_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    cursor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="IDLE", nullable=False, index=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackfillRecord(TimestampMixin, Base):
    """Per-entity checkpoint that makes priority backfills resumable and retryable."""

    __tablename__ = "backfill_records"
    __table_args__ = (
        UniqueConstraint("operation", "entity_type", "entity_id", name="uq_backfill_entity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
