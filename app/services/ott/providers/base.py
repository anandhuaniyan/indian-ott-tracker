"""Normalized contracts shared by every OTT intelligence provider."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Protocol


AVAILABILITY_TYPES = {
    "subscription": "SUBSCRIPTION",
    "sub": "SUBSCRIPTION",
    "flatrate": "SUBSCRIPTION",
    "free": "FREE",
    "ads": "ADS",
    "ad": "ADS",
    "rent": "RENT",
    "buy": "BUY",
    "addon": "CHANNEL",
    "channel": "CHANNEL",
    "tve": "CHANNEL",
}


def normalize_availability_type(value: str | None) -> str:
    return AVAILABILITY_TYPES.get((value or "").strip().lower(), "UNKNOWN")


def normalize_date(value) -> date | None:
    """Accept provider date objects or ISO strings; reject ambiguous formats."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return None


@dataclass(slots=True)
class NormalizedOttEvidence:
    source_type: str
    source_name: str
    country: str = "IN"
    fact_type: str = "AVAILABILITY"
    platform_candidate: str | None = None
    release_date_candidate: date | None = None
    availability_type: str = "UNKNOWN"
    source_url: str | None = None
    source_published_at: date | None = None
    observed_at: datetime | None = None
    raw_external_id: str | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    title: str | None = None
    original_title: str | None = None
    year: int | None = None
    language: str | None = None
    directors: tuple[str, ...] = ()
    cast: tuple[str, ...] = ()
    runtime_minutes: int | None = None
    movie_match_confidence: float = 0
    platform_confidence: float = 0
    date_confidence: float = 0
    inspected: bool = True
    verification_method: str = "AUTOMATED"
    notes: str | None = None

    def serializable(self) -> dict:
        value = asdict(self)
        for key in ("release_date_candidate", "source_published_at", "observed_at"):
            if value[key] is not None:
                value[key] = value[key].isoformat()
        return value

    @classmethod
    def from_serializable(cls, value: dict):
        data = dict(value)
        if data.get("release_date_candidate"):
            data["release_date_candidate"] = date.fromisoformat(data["release_date_candidate"][:10])
        if data.get("source_published_at"):
            data["source_published_at"] = date.fromisoformat(data["source_published_at"][:10])
        if data.get("observed_at"):
            data["observed_at"] = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
        data["directors"] = tuple(data.get("directors") or ())
        data["cast"] = tuple(data.get("cast") or ())
        return cls(**data)


class OttProvider(Protocol):
    name: str
    enabled: bool
    configured: bool
    daily_limit: int
    monthly_limit: int

    def fetch_movie(self, movie) -> list[NormalizedOttEvidence]: ...


class ProviderError(RuntimeError):
    status = "DOWN"


class ProviderDisabled(ProviderError):
    status = "DISABLED"


class ProviderRateLimited(ProviderError):
    status = "RATE_LIMITED"


class ProviderQuotaExhausted(ProviderError):
    status = "QUOTA_EXHAUSTED"
