"""Pydantic schemas for OTT platforms."""

from pydantic import BaseModel, ConfigDict, Field


class OttPlatformBase(BaseModel):
    """Shared OTT platform fields."""

    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100)
    logo_url: str | None = Field(default=None, max_length=500)
    website_url: str | None = Field(default=None, max_length=500)
    is_active: bool = True


class OttPlatformCreate(OttPlatformBase):
    """Payload for creating an OTT platform."""


class OttPlatformRead(OttPlatformBase):
    """OTT platform returned from the database."""

    model_config = ConfigDict(from_attributes=True)

    id: int
