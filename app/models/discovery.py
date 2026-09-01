"""Persistent checkpoints and review records for new-movie discovery."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class MovieDiscoveryRun(TimestampMixin, Base):
    """One independently auditable morning, evening, weekly, or manual scan."""

    __tablename__ = "movie_discovery_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    slot: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    languages: Mapped[list] = mapped_column(JSON, nullable=False)
    candidates_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    already_existing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_movies_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    language_stats: Mapped[dict | None] = mapped_column(JSON)
    source_stats: Mapped[dict | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(Text)


class MovieDiscoveryCandidate(TimestampMixin, Base):
    """A stable source identity and its current import/review disposition."""

    __tablename__ = "movie_discovery_candidates"
    __table_args__ = (
        UniqueConstraint("source", "external_key", name="uq_movie_discovery_candidate_source_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    latest_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("movie_discovery_runs.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    external_key: Mapped[str] = mapped_column(String(180), nullable=False)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    original_title: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str | None] = mapped_column(String(20), index=True)
    release_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DISCOVERED", index=True)
    matched_movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="SET NULL"), index=True
    )
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    match_reason: Mapped[str | None] = mapped_column(String(500))
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
