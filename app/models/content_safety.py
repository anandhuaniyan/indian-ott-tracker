"""Auditable content-safety visibility transitions."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class ContentSafetyAudit(TimestampMixin, Base):
    __tablename__ = "content_safety_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    original_title: Mapped[str | None] = mapped_column(String(500))
    release_date: Mapped[date | None] = mapped_column(Date)
    language: Mapped[str | None] = mapped_column(String(10), index=True)
    previous_classification: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    new_classification: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    tmdb_adult: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    certification: Mapped[str | None] = mapped_column(String(50))
    reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    classification_source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    public_visibility_before: Mapped[str] = mapped_column(String(20), nullable=False)
    public_visibility_after: Mapped[str] = mapped_column(String(20), nullable=False)
    notification_status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    notification_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notification_error: Mapped[str | None] = mapped_column(Text)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
