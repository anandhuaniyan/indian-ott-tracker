"""TV show model."""

from datetime import date

from sqlalchemy import Boolean, Date, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.genre import tv_show_genres
from app.models.language import tv_show_languages
from app.models.mixins import TimestampMixin


class TVShow(TimestampMixin, Base):
    """A TV series, typically sourced from TMDB."""

    __tablename__ = "tv_shows"

    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    original_name: Mapped[str | None] = mapped_column(String(500))
    overview: Mapped[str | None] = mapped_column(Text)
    first_air_date: Mapped[date | None] = mapped_column(Date, index=True)
    last_air_date: Mapped[date | None] = mapped_column(Date)
    number_of_seasons: Mapped[int | None] = mapped_column(Integer)
    number_of_episodes: Mapped[int | None] = mapped_column(Integer)
    poster_path: Mapped[str | None] = mapped_column(String(500))
    backdrop_path: Mapped[str | None] = mapped_column(String(500))
    popularity: Mapped[float | None] = mapped_column(Float)
    vote_average: Mapped[float | None] = mapped_column(Float)
    vote_count: Mapped[int | None] = mapped_column(Integer)
    original_language: Mapped[str | None] = mapped_column(String(10))
    adult: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str | None] = mapped_column(String(50))

    genres: Mapped[list["Genre"]] = relationship(
        secondary=tv_show_genres,
        back_populates="tv_shows",
    )
    languages: Mapped[list["Language"]] = relationship(
        secondary=tv_show_languages,
        back_populates="tv_shows",
    )
    ott_availability: Mapped[list["TVShowOtt"]] = relationship(
        back_populates="tv_show",
        cascade="all, delete-orphan",
    )
