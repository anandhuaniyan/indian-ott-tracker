"""TV show OTT availability model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import AvailabilityType, availability_type_enum
from app.models.mixins import TimestampMixin


class TVShowOtt(TimestampMixin, Base):
    """OTT availability record for a TV show on a specific platform."""

    __tablename__ = "tv_show_ott_availability"
    __table_args__ = (
        UniqueConstraint("tv_show_id", "platform_id", "region", name="uq_tv_show_platform_region"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tv_show_id: Mapped[int] = mapped_column(
        ForeignKey("tv_shows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform_id: Mapped[int] = mapped_column(
        ForeignKey("ott_platforms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    watch_url: Mapped[str | None] = mapped_column(String(1000))
    availability_type: Mapped[AvailabilityType] = mapped_column(
        availability_type_enum,
        default=AvailabilityType.STREAM,
        nullable=False,
    )
    region: Mapped[str] = mapped_column(String(10), default="IN", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tv_show: Mapped["TVShow"] = relationship(back_populates="ott_availability")
    platform: Mapped["OttPlatform"] = relationship(back_populates="tv_show_availability")
