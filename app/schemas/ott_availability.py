"""Pydantic schemas for OTT Availability Tracking System."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OttProviderItem(BaseModel):
    """An individual provider availability entry in API responses."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., description="OTT Platform name (e.g. Netflix, Prime Video)")
    country: str = Field(default="IN", description="ISO country code")
    watch_type: str = Field(default="subscription", description="Watch type: subscription, free, rent, buy")
    source: str = Field(default="TMDB", description="Data source: TMDB, GOOGLE_SEARCH, MANUAL")
    provider_logo: str | None = Field(default=None, description="Platform logo URL")
    source_url: str | None = Field(default=None, description="Direct watch or source URL")


class OttAvailabilitySummary(BaseModel):
    """Aggregated OTT availability object included in Movie response payload."""

    model_config = ConfigDict(from_attributes=True)

    available: bool = Field(default=False, description="True if content is available on any OTT platform")
    ott_release_date: date | None = Field(default=None, description="Earliest known OTT release date")
    last_checked: datetime | None = Field(default=None, description="Timestamp when availability was last checked")
    providers: list[OttProviderItem] = Field(default_factory=list, description="List of active OTT providers")


class OttAvailabilityBase(BaseModel):
    """Base fields for OttAvailability DB records."""

    provider: str = Field(..., max_length=100)
    provider_logo: str | None = Field(default=None, max_length=500)
    country: str = Field(default="IN", max_length=10)
    watch_type: str = Field(default="subscription", max_length=50)
    ott_release_date: date | None = None
    status: str = Field(default="available", max_length=50)
    source_type: str = Field(default="tmdb", max_length=50)
    source_url: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=100.0, ge=0.0, le=100.0)
    last_checked: datetime | None = None
    verification_status: str = Field(default="UNKNOWN", max_length=20)
    verified_at: datetime | None = None
    manually_verified: bool = False
    evidence_id: int | None = None


class OttAvailabilityCreate(OttAvailabilityBase):
    """Payload for creating or updating an OTT availability record."""

    movie_id: int


class OttAvailabilityRead(OttAvailabilityBase):
    """OttAvailability returned from DB."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    movie_id: int
    created_at: datetime
    updated_at: datetime
