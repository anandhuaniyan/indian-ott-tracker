"""Auditable research runs and idempotent request communications."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class ResearchRun(TimestampMixin, Base):
    """One manual or scheduled research operation with durable provenance."""

    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    trigger_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    initiated_by: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED", index=True)
    result: Mapped[str | None] = mapped_column(String(30), index=True)
    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="SET NULL"), index=True
    )
    request_id: Mapped[str | None] = mapped_column(String(32), index=True)
    active_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    queries_attempted: Mapped[list | None] = mapped_column(JSON)
    providers_attempted: Mapped[list | None] = mapped_column(JSON)
    web_searches_attempted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sources_discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evidence_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    before_platform: Mapped[str | None] = mapped_column(String(100))
    after_platform: Mapped[str | None] = mapped_column(String(100))
    before_release_date: Mapped[date | None] = mapped_column(Date)
    after_release_date: Mapped[date | None] = mapped_column(Date)
    before_imdb_rating: Mapped[float | None] = mapped_column(Float)
    after_imdb_rating: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    database_changes: Mapped[list | None] = mapped_column(JSON)
    notification_results: Mapped[dict | None] = mapped_column(JSON)
    errors: Mapped[list | None] = mapped_column(JSON)
    details: Mapped[dict | None] = mapped_column(JSON)


class RequestCommunication(TimestampMixin, Base):
    """Idempotent delivery status for request emails and chat notifications."""

    __tablename__ = "request_communications"
    __table_args__ = (
        UniqueConstraint("movie_request_id", "event_type", "channel", name="uq_request_communication_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_request_id: Mapped[int] = mapped_column(
        ForeignKey("movie_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(500))
    fingerprint: Mapped[str | None] = mapped_column(String(128), index=True)
