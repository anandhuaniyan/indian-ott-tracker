"""Pydantic schemas for languages."""

from pydantic import BaseModel, ConfigDict, Field


class LanguageBase(BaseModel):
    """Shared language fields."""

    iso_639_1: str = Field(..., min_length=2, max_length=2)
    english_name: str = Field(..., max_length=100)
    native_name: str | None = Field(default=None, max_length=100)


class LanguageCreate(LanguageBase):
    """Payload for creating a language."""


class LanguageRead(LanguageBase):
    """Language returned from the database."""

    model_config = ConfigDict(from_attributes=True)

    id: int
