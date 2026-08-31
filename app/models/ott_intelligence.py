"""Evidence-first OTT intelligence models.

These tables are additive.  They preserve observations, reconciliation history,
provider health/budgets, cached responses, and the manually curated gold set
without replacing the existing canonical ``ott_availability`` table.
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class OttAvailabilityObservation(TimestampMixin, Base):
    __tablename__ = "ott_availability_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str | None] = mapped_column(String(100), index=True)
    country: Mapped[str] = mapped_column(String(10), default="IN", nullable=False, index=True)
    availability_type: Mapped[str] = mapped_column(String(30), default="UNKNOWN", nullable=False, index=True)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    raw_external_id: Mapped[str | None] = mapped_column(String(200), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("ott_evidence.id", ondelete="SET NULL"), index=True)
    details: Mapped[dict | None] = mapped_column(JSON)


class OttReconciliationDecision(TimestampMixin, Base):
    __tablename__ = "ott_reconciliation_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    country: Mapped[str] = mapped_column(String(10), default="IN", nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    platform: Mapped[str | None] = mapped_column(String(100), index=True)
    release_date: Mapped[date | None] = mapped_column(Date, index=True)
    availability_type: Mapped[str | None] = mapped_column(String(30))
    platform_confidence: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    date_confidence: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    movie_match_confidence: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    health_score: Mapped[float] = mapped_column(Float, default=0, nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    supporting_evidence_ids: Mapped[list | None] = mapped_column(JSON)
    conflicting_evidence_ids: Mapped[list | None] = mapped_column(JSON)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class OttProviderBudgetPeriod(TimestampMixin, Base):
    __tablename__ = "ott_provider_budget_periods"
    __table_args__ = (
        UniqueConstraint("provider", "period_type", "period_key", name="uq_ott_provider_budget_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)
    period_key: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    request_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OttProviderHealth(TimestampMixin, Base):
    __tablename__ = "ott_provider_health"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="DISABLED", nullable=False, index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    match_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class OttProviderCache(TimestampMixin, Base):
    __tablename__ = "ott_provider_cache"
    __table_args__ = (
        UniqueConstraint("provider", "cache_key", name="uq_ott_provider_cache"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    cache_key: Mapped[str] = mapped_column(String(250), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class OttGoldSetCase(TimestampMixin, Base):
    __tablename__ = "ott_gold_set_cases"
    __table_args__ = (UniqueConstraint("movie_id", name="uq_ott_gold_set_movie"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    expected_platform: Mapped[str | None] = mapped_column(String(100))
    expected_release_date: Mapped[date | None] = mapped_column(Date)
    expected_availability_type: Mapped[str | None] = mapped_column(String(30))
    expected_state: Mapped[str] = mapped_column(String(40), default="UNKNOWN", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    notes: Mapped[str | None] = mapped_column(Text)
    manually_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
