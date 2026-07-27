"""Pydantic schemas for TV shows."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.genre import GenreRead
from app.schemas.language import LanguageRead


class TVShowBase(BaseModel):
    """Shared TV show fields."""

    tmdb_id: int
    name: str = Field(..., max_length=500)
    original_name: str | None = Field(default=None, max_length=500)
    overview: str | None = None
    first_air_date: date | None = None
    last_air_date: date | None = None
    number_of_seasons: int | None = None
    number_of_episodes: int | None = None
    poster_path: str | None = Field(default=None, max_length=500)
    backdrop_path: str | None = Field(default=None, max_length=500)
    popularity: float | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    original_language: str | None = Field(default=None, max_length=10)
    adult: bool = False
    status: str | None = Field(default=None, max_length=50)


class TVShowCreate(TVShowBase):
    """Payload for creating a TV show."""


class TVShowRead(TVShowBase):
    """TV show returned from the database."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    genres: list[GenreRead] = []
    languages: list[LanguageRead] = []
