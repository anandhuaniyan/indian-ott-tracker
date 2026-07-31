"""OTT Availability model for tracking movie streaming availability and fallback metadata."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import TimestampMixin


class OttAvailability(TimestampMixin, Base):
    """Detailed OTT availability tracking record for a movie."""

    __tablename__ = "ott_availability"
    __table_args__ = (
        UniqueConstraint(
            "movie_id",
            "provider",
            "country",
            "watch_type",
            name="uq_movie_ott_provider_country_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    provider_logo: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str] = mapped_column(String(10), default="IN", nullable=False)
    watch_type: Mapped[str] = mapped_column(String(50), default="subscription", nullable=False)
    ott_release_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(50), default="available", nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), default="tmdb", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    movie: Mapped["Movie"] = relationship(back_populates="ott_availabilities")
