"""Pydantic schemas for TV show OTT availability."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AvailabilityType
from app.schemas.ott_platform import OttPlatformRead


class TVShowOttBase(BaseModel):
    """Shared TV show availability fields."""

    watch_url: str | None = Field(default=None, max_length=1000)
    availability_type: AvailabilityType = AvailabilityType.STREAM
    region: str = Field(default="IN", max_length=10)
    last_checked_at: datetime | None = None


class TVShowOttCreate(TVShowOttBase):
    """Payload for creating TV show availability."""

    tv_show_id: int
    platform_id: int


class TVShowOttRead(TVShowOttBase):
    """TV show availability returned from the database."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tv_show_id: int
    platform_id: int
    platform: OttPlatformRead | None = None
