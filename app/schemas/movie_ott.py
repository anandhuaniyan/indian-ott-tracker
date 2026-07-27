"""Pydantic schemas for movie OTT availability."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AvailabilityType
from app.schemas.ott_platform import OttPlatformRead


class MovieOttBase(BaseModel):
    """Shared movie availability fields."""

    watch_url: str | None = Field(default=None, max_length=1000)
    availability_type: AvailabilityType = AvailabilityType.STREAM
    region: str = Field(default="IN", max_length=10)
    last_checked_at: datetime | None = None


class MovieOttCreate(MovieOttBase):
    """Payload for creating movie availability."""

    movie_id: int
    platform_id: int


class MovieOttRead(MovieOttBase):
    """Movie availability returned from the database."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    movie_id: int
    platform_id: int
    platform: OttPlatformRead | None = None
