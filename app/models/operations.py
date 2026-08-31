"""Additive operational models for requests, evidence, health and notifications."""

from datetime import date, datetime
from sqlalchemy import (
    Boolean,
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    local_movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="SET NULL"), index=True
    )
    movie_existed_at_submission: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    public_rejection_reason: Mapped[str | None] = mapped_column(Text)
    internal_rejection_reason: Mapped[str | None] = mapped_column(Text)
    confirmation_email_status: Mapped[str] = mapped_column(
        String(20), default="PENDING"
    )
    confirmation_email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    confirmation_email_last_error: Mapped[str | None] = mapped_column(Text)
    confirmation_email_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completion_email_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    completion_email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completion_email_last_error: Mapped[str | None] = mapped_column(Text)
    completion_email_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    rejection_email_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    rejection_email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    rejection_email_last_error: Mapped[str | None] = mapped_column(Text)
    rejection_email_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    confirmation_email_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    completion_email_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    rejection_email_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    admin_notification_email_status: Mapped[str] = mapped_column(
        String(20), default="PENDING"
    )
    admin_notification_email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    admin_notification_email_last_error: Mapped[str | None] = mapped_column(Text)
    admin_notification_email_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    admin_notification_email_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    sla_36_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_48_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MovieComment(TimestampMixin, Base):
    __tablename__ = "movie_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    comment_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="PENDING", nullable=False, index=True
    )
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderation_reason: Mapped[str | None] = mapped_column(Text)


class OttEvidence(TimestampMixin, Base):
    __tablename__ = "ott_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="UNKNOWN", index=True)
    platform: Mapped[str | None] = mapped_column(String(100), index=True)
    release_date: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_name: Mapped[str | None] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(
        String(50), default="unknown", nullable=False, index=True
    )
    country: Mapped[str] = mapped_column(String(10), default="IN", nullable=False)
    source_title: Mapped[str | None] = mapped_column(String(500))
    source_published_at: Mapped[date | None] = mapped_column(Date)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manually_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    trusted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class DataQualityIssue(TimestampMixin, Base):
    __tablename__ = "data_quality_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), index=True
    )
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
    status: Mapped[str] = mapped_column(
        String(20), default="IDLE", nullable=False, index=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict | None] = mapped_column(JSON)


class AdminAuditLog(TimestampMixin, Base):
    """Small, secret-free history of deliberate administrator actions."""

    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_id: Mapped[str | None] = mapped_column(String(100), index=True)
    summary: Mapped[str | None] = mapped_column(String(1000))


class OttSourceRelease(TimestampMixin, Base):
    """Normalized record received from an explicitly configured OTT adapter."""

    __tablename__ = "ott_source_releases"
    __table_args__ = (
        UniqueConstraint("source", "external_key", name="uq_ott_source_release"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    external_key: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    original_title: Mapped[str | None] = mapped_column(String(500))
    platform: Mapped[str | None] = mapped_column(String(100), index=True)
    release_date: Mapped[date | None] = mapped_column(Date, index=True)
    language: Mapped[str | None] = mapped_column(String(20), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(
        String(20), default="UNMATCHED", nullable=False, index=True
    )
    matched_movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="SET NULL"), index=True
    )
    match_reason: Mapped[str | None] = mapped_column(String(500))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackfillRecord(TimestampMixin, Base):
    """Per-entity checkpoint that makes priority backfills resumable and retryable."""

    __tablename__ = "backfill_records"
    __table_args__ = (
        UniqueConstraint(
            "operation", "entity_type", "entity_id", name="uq_backfill_entity"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="PENDING", nullable=False, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
