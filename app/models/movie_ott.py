"""Legacy Movie OTT availability model.

This model is kept temporarily for database compatibility.
The active OTT tracking system uses OttAvailability.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import AvailabilityType, availability_type_enum
from app.models.mixins import TimestampMixin


class MovieOtt(TimestampMixin, Base):
    """Legacy OTT availability record."""

    __tablename__ = "movie_ott_availability"

    __table_args__ = (
        UniqueConstraint(
            "movie_id",
            "platform_id",
            "region",
            name="uq_movie_platform_region",
        ),
    )


    id: Mapped[int] = mapped_column(
        primary_key=True
    )


    movie_id: Mapped[int] = mapped_column(
        ForeignKey(
            "movies.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )


    platform_id: Mapped[int] = mapped_column(
        ForeignKey(
            "ott_platforms.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )


    watch_url: Mapped[str | None] = mapped_column(
        String(1000)
    )


    availability_type: Mapped[AvailabilityType] = mapped_column(
        availability_type_enum,
        default=AvailabilityType.STREAM,
        nullable=False,
    )


    region: Mapped[str] = mapped_column(
        String(10),
        default="IN",
        nullable=False,
    )


    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


    # Keep relationship independent.
    # Main application uses OttAvailability.
    movie: Mapped["Movie"] = relationship()

    platform: Mapped["OttPlatform"] = relationship(
        back_populates="movie_availability"
    )