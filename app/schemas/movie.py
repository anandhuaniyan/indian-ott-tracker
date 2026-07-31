"""Pydantic schemas for movies."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.genre import GenreRead
from app.schemas.language import LanguageRead
from app.schemas.ott_availability import OttAvailabilitySummary, OttProviderItem


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
    ott_availability: OttAvailabilitySummary = Field(
        default_factory=OttAvailabilitySummary,
        description="OTT Availability tracking summary",
    )

    @model_validator(mode="before")
    @classmethod
    def populate_ott_summary(cls, data: Any) -> Any:
        """Automatically convert SQLAlchemy Movie.ott_availabilities into OttAvailabilitySummary."""
        if hasattr(data, "ott_availabilities"):
            records = getattr(data, "ott_availabilities", []) or []
            providers = []
            earliest_release_date: date | None = None
            latest_last_checked: datetime | None = None

            for rec in records:
                providers.append(
                    OttProviderItem(
                        name=getattr(rec, "provider", ""),
                        country=getattr(rec, "country", "IN"),
                        watch_type=getattr(rec, "watch_type", "subscription"),
                        source=getattr(rec, "source_type", "TMDB"),
                        provider_logo=getattr(rec, "provider_logo", None),
                        source_url=getattr(rec, "source_url", None),
                    )
                )

                r_date = getattr(rec, "ott_release_date", None)
                if r_date:
                    if earliest_release_date is None or r_date < earliest_release_date:
                        earliest_release_date = r_date

                l_checked = getattr(rec, "last_checked", None)
                if l_checked:
                    if latest_last_checked is None or l_checked > latest_last_checked:
                        latest_last_checked = l_checked

            summary = OttAvailabilitySummary(
                available=len(providers) > 0,
                ott_release_date=earliest_release_date,
                last_checked=latest_last_checked,
                providers=providers,
            )

            # Build a dictionary to avoid triggering SQLAlchemy ORM attribute assignment
            return {
                "id": getattr(data, "id"),
                "tmdb_id": getattr(data, "tmdb_id"),
                "title": getattr(data, "title"),
                "original_title": getattr(data, "original_title", None),
                "overview": getattr(data, "overview", None),
                "release_date": getattr(data, "release_date", None),
                "runtime_minutes": getattr(data, "runtime_minutes", None),
                "poster_path": getattr(data, "poster_path", None),
                "backdrop_path": getattr(data, "backdrop_path", None),
                "popularity": getattr(data, "popularity", None),
                "vote_average": getattr(data, "vote_average", None),
                "vote_count": getattr(data, "vote_count", None),
                "original_language": getattr(data, "original_language", None),
                "adult": getattr(data, "adult", False),
                "genres": getattr(data, "genres", []),
                "languages": getattr(data, "languages", []),
                "ott_availability": summary,
            }

        return data