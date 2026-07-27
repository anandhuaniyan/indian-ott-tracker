"""Language model."""

from sqlalchemy import String, Table, Column, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.mixins import TimestampMixin


movie_languages = Table(
    "movie_languages",
    Base.metadata,
    Column("movie_id", ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("language_id", ForeignKey("languages.id", ondelete="CASCADE"), primary_key=True),
)

tv_show_languages = Table(
    "tv_show_languages",
    Base.metadata,
    Column("tv_show_id", ForeignKey("tv_shows.id", ondelete="CASCADE"), primary_key=True),
    Column("language_id", ForeignKey("languages.id", ondelete="CASCADE"), primary_key=True),
)


class Language(TimestampMixin, Base):
    """A spoken language associated with content."""

    __tablename__ = "languages"

    id: Mapped[int] = mapped_column(primary_key=True)
    iso_639_1: Mapped[str] = mapped_column(String(2), unique=True, nullable=False, index=True)
    english_name: Mapped[str] = mapped_column(String(100), nullable=False)
    native_name: Mapped[str | None] = mapped_column(String(100))

    movies: Mapped[list["Movie"]] = relationship(
        secondary=movie_languages,
        back_populates="languages",
    )
    tv_shows: Mapped[list["TVShow"]] = relationship(
        secondary=tv_show_languages,
        back_populates="languages",
    )
