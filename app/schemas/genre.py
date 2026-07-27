"""Pydantic schemas for genres."""

from pydantic import BaseModel, ConfigDict, Field


class GenreBase(BaseModel):
    """Shared genre fields."""

    tmdb_id: int | None = None
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100)


class GenreCreate(GenreBase):
    """Payload for creating a genre."""


class GenreRead(GenreBase):
    """Genre returned from the database."""

    model_config = ConfigDict(from_attributes=True)

    id: int
