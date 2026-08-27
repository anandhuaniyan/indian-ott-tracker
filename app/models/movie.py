"""Movie model."""

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.genre import movie_genres
from app.models.language import movie_languages
from app.models.mixins import TimestampMixin


class Movie(TimestampMixin, Base):
    """A movie title, typically sourced from TMDB."""

    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(primary_key=True)

    tmdb_id: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        index=True,
    )

    original_title: Mapped[str | None] = mapped_column(String(500))

    overview: Mapped[str | None] = mapped_column(Text)

    release_date: Mapped[date | None] = mapped_column(
        Date,
        index=True,
    )

    runtime_minutes: Mapped[int | None] = mapped_column(Integer)

    poster_path: Mapped[str | None] = mapped_column(String(500))

    backdrop_path: Mapped[str | None] = mapped_column(String(500))

    popularity: Mapped[float | None] = mapped_column(Float)

    vote_average: Mapped[float | None] = mapped_column(Float)

    vote_count: Mapped[int | None] = mapped_column(Integer)

    original_language: Mapped[str | None] = mapped_column(String(10))

    adult: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    status: Mapped[str | None] = mapped_column(String(50))

    # Locally derived from stored release records. This deliberately remains
    # separate from TMDB's production ``status`` field above.
    release_status_code: Mapped[str | None] = mapped_column(String(32), index=True)
    theatrical_release_date: Mapped[date | None] = mapped_column(Date, index=True)
    ott_research_eligibility: Mapped[str | None] = mapped_column(String(32), index=True)
    release_classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tagline: Mapped[str | None] = mapped_column(Text)
    budget: Mapped[int | None] = mapped_column(BigInteger)
    revenue: Mapped[int | None] = mapped_column(BigInteger)
    collection_tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    collection_name: Mapped[str | None] = mapped_column(String(500))
    collection_poster_path: Mapped[str | None] = mapped_column(String(500))
    collection_backdrop_path: Mapped[str | None] = mapped_column(String(500))


    genres: Mapped[list["Genre"]] = relationship(
        secondary=movie_genres,
        back_populates="movies",
    )


    languages: Mapped[list["Language"]] = relationship(
        secondary=movie_languages,
        back_populates="movies",
    )


    # New OTT availability system
    ott_availabilities: Mapped[list["OttAvailability"]] = relationship(
        back_populates="movie",
        cascade="all, delete-orphan",
    )

    credits: Mapped[list["MovieCredit"]] = relationship(back_populates="movie", cascade="all, delete-orphan")
    external_ids: Mapped[list["ExternalId"]] = relationship(back_populates="movie", cascade="all, delete-orphan")
    release_dates: Mapped[list["MovieReleaseDate"]] = relationship(back_populates="movie", cascade="all, delete-orphan")
    images: Mapped[list["MovieImage"]] = relationship(back_populates="movie", cascade="all, delete-orphan")
    ratings: Mapped[list["MovieRating"]] = relationship(back_populates="movie", cascade="all, delete-orphan")
    alternative_titles: Mapped[list["AlternativeTitle"]] = relationship(back_populates="movie", cascade="all, delete-orphan")
