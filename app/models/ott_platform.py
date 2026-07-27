"""OTT platform model."""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import TimestampMixin


class OttPlatform(TimestampMixin, Base):
    """An Indian OTT streaming service."""

    __tablename__ = "ott_platforms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    logo_url: Mapped[str | None] = mapped_column(String(500))
    website_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    movie_availability: Mapped[list["MovieOtt"]] = relationship(
        back_populates="platform",
        cascade="all, delete-orphan",
    )
    tv_show_availability: Mapped[list["TVShowOtt"]] = relationship(
        back_populates="platform",
        cascade="all, delete-orphan",
    )
