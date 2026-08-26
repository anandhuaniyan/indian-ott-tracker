"""Normalized, movie-only metadata models populated from legitimate providers."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import TimestampMixin


class Person(TimestampMixin, Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    profile_path: Mapped[str | None] = mapped_column(String(500))
    known_for_department: Mapped[str | None] = mapped_column(String(100))

    credits: Mapped[list["MovieCredit"]] = relationship(back_populates="person", cascade="all, delete-orphan")


class MovieCredit(TimestampMixin, Base):
    __tablename__ = "movie_credits"
    __table_args__ = (UniqueConstraint("movie_id", "person_id", "credit_type", "job", "character", name="uq_movie_credit"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    tmdb_credit_id: Mapped[str | None] = mapped_column(String(100), index=True)
    credit_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # cast or crew
    character: Mapped[str | None] = mapped_column(String(500))
    cast_order: Mapped[int | None] = mapped_column(Integer)
    department: Mapped[str | None] = mapped_column(String(100))
    job: Mapped[str | None] = mapped_column(String(255))

    movie: Mapped["Movie"] = relationship(back_populates="credits")
    person: Mapped[Person] = relationship(back_populates="credits")


class Keyword(TimestampMixin, Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)


class MovieKeyword(Base):
    __tablename__ = "movie_keywords"

    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    keyword_id: Mapped[int] = mapped_column(ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True)


class ProductionCompany(TimestampMixin, Base):
    __tablename__ = "production_companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    logo_path: Mapped[str | None] = mapped_column(String(500))
    origin_country: Mapped[str | None] = mapped_column(String(2))


class MovieProductionCompany(Base):
    __tablename__ = "movie_production_companies"

    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    production_company_id: Mapped[int] = mapped_column(ForeignKey("production_companies.id", ondelete="CASCADE"), primary_key=True)


class ProductionCountry(TimestampMixin, Base):
    __tablename__ = "production_countries"

    id: Mapped[int] = mapped_column(primary_key=True)
    iso_3166_1: Mapped[str] = mapped_column(String(2), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class MovieProductionCountry(Base):
    __tablename__ = "movie_production_countries"

    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    production_country_id: Mapped[int] = mapped_column(ForeignKey("production_countries.id", ondelete="CASCADE"), primary_key=True)


class ExternalId(TimestampMixin, Base):
    __tablename__ = "external_ids"
    __table_args__ = (
        UniqueConstraint("movie_id", "provider", name="uq_movie_external_id_provider"),
        UniqueConstraint("provider", "external_id", name="uq_external_id_provider_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(String(1000))

    movie: Mapped["Movie"] = relationship(back_populates="external_ids")


class MovieReleaseDate(TimestampMixin, Base):
    __tablename__ = "movie_release_dates"
    __table_args__ = (UniqueConstraint("movie_id", "country", "release_date", "release_type", name="uq_movie_release"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    release_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    release_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    certification: Mapped[str | None] = mapped_column(String(50))
    note: Mapped[str | None] = mapped_column(Text)

    movie: Mapped["Movie"] = relationship(back_populates="release_dates")


class MovieImage(TimestampMixin, Base):
    __tablename__ = "movie_images"
    __table_args__ = (UniqueConstraint("movie_id", "image_type", "source", "source_id", name="uq_movie_image_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    image_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(255))
    original_url: Mapped[str | None] = mapped_column(String(1000))
    local_path: Mapped[str | None] = mapped_column(String(1000), index=True)
    language: Mapped[str | None] = mapped_column(String(10))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    aspect_ratio: Mapped[float | None] = mapped_column(Float)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    movie: Mapped["Movie"] = relationship(back_populates="images")


class MovieRating(TimestampMixin, Base):
    __tablename__ = "movie_ratings"
    __table_args__ = (UniqueConstraint("movie_id", "source", name="uq_movie_rating_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    rating: Mapped[float | None] = mapped_column(Float)
    vote_count: Mapped[int | None] = mapped_column(Integer)
    last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    movie: Mapped["Movie"] = relationship(back_populates="ratings")
