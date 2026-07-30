"""Pydantic schemas for movies."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.genre import GenreRead
from app.schemas.language import LanguageRead
from app.schemas.ott import MovieOTTRead


class MovieBase(BaseModel):
    """Shared movie fields."""

    tmdb_id: int
    title: str = Field(..., max_length=500)
    original_title: str | None = Field(default=None, max_length=500)
    overview: str | None = None
    release_date: date | None = None
    runtime_minutes: int | None = None
    poster_path: str | None = Field(default=None, max_length=500)
    backdrop_path: str | None = Field(default=None, max_length=500)
    popularity: float | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    original_language: str | None = Field(default=None, max_length=10)
    adult: bool = False


class MovieCreate(MovieBase):
    """Payload for creating a movie."""


class MovieRead(MovieBase):
    """Movie returned from the database."""

    model_config = ConfigDict(
        from_attributes=True
    )

    id: int

    genres: list[GenreRead] = []

    languages: list[LanguageRead] = []

    ott_availability: list[MovieOTTRead] = []