"""Movie of the Night Streaming Availability API adapter for India."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.config.settings import settings
from app.services.ott.providers.base import NormalizedOttEvidence, ProviderQuotaExhausted, ProviderRateLimited, normalize_availability_type
from app.services.ott_providers import normalize_platform


def _imdb_id(movie):
    return next((item.external_id for item in movie.external_ids if (item.provider or "").lower() == "imdb"), None)


class StreamingAvailabilityProvider:
    name = "streaming_availability"
    daily_limit = settings.STREAMING_AVAILABILITY_DAILY_LIMIT
    monthly_limit = settings.STREAMING_AVAILABILITY_MONTHLY_LIMIT

    @property
    def enabled(self):
        return settings.STREAMING_AVAILABILITY_ENABLED

    @property
    def configured(self):
        return bool(self.enabled and settings.STREAMING_AVAILABILITY_API_KEY)

    def fetch_movie(self, movie) -> list[NormalizedOttEvidence]:
        identifier = _imdb_id(movie) or f"movie/{movie.tmdb_id}"
        response = httpx.get(
            f"{settings.STREAMING_AVAILABILITY_BASE_URL.rstrip('/')}/shows/{identifier}",
            params={"country": "in", "series_granularity": "show"},
            headers={"X-API-Key": settings.STREAMING_AVAILABILITY_API_KEY, "Accept": "application/json"},
            timeout=20,
            follow_redirects=True,
        )
        if response.status_code == 429:
            raise ProviderRateLimited("Streaming Availability rate limit reached")
        if response.status_code in {402, 403}:
            raise ProviderQuotaExhausted("Streaming Availability quota or plan rejected the request")
        response.raise_for_status()
        payload = response.json()
        options = payload.get("streamingOptions", {}).get("in") or payload.get("streamingOptions", {}).get("IN") or []
        now = datetime.now(timezone.utc)
        result = []
        for item in options:
            service = item.get("service") or {}
            platform = normalize_platform(service.get("name") or service.get("id"))
            if not platform:
                continue
            observed = now
            if item.get("availableSince"):
                try:
                    observed = datetime.fromtimestamp(int(item["availableSince"]), timezone.utc)
                except (TypeError, ValueError, OSError):
                    pass
            result.append(
                NormalizedOttEvidence(
                    source_type="STREAMING_AVAILABILITY",
                    source_name="Streaming Availability API",
                    fact_type="AVAILABILITY",
                    platform_candidate=platform,
                    availability_type=normalize_availability_type(item.get("type")),
                    source_url=item.get("link") or item.get("videoLink") or "https://www.movieofthenight.com/",
                    observed_at=observed,
                    raw_external_id=str(service.get("id") or payload.get("id") or "") or None,
                    tmdb_id=movie.tmdb_id,
                    imdb_id=payload.get("imdbId") or _imdb_id(movie),
                    title=payload.get("title") or movie.title,
                    original_title=payload.get("originalTitle"),
                    year=payload.get("releaseYear"),
                    language=movie.original_language,
                    directors=tuple(payload.get("directors") or ()),
                    cast=tuple(payload.get("cast") or ()),
                    runtime_minutes=payload.get("runtime"),
                    movie_match_confidence=100,
                    platform_confidence=75,
                    date_confidence=0,
                    notes="availableSince is an observation boundary, not an OTT premiere date",
                )
            )
        return result
