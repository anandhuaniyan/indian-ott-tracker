"""Editorial updates and public movie-correction reports."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.mixins import TimestampMixin


class EditorialArticle(TimestampMixin, Base):
    __tablename__ = "editorial_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(40), default="EDITORIAL", nullable=False, index=True)
    author_name: Mapped[str] = mapped_column(String(255), default="OTTTracker Editorial", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    seo_title: Mapped[str | None] = mapped_column(String(255))
    seo_description: Mapped[str | None] = mapped_column(String(500))
    hero_image: Mapped[str | None] = mapped_column(String(1000))
    source_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_indexable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ads_eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MovieCorrection(TimestampMixin, Base):
    __tablename__ = "movie_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False, index=True)
