"""Optional Watchmode India availability adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.config.settings import settings
from app.services.ott.providers.base import NormalizedOttEvidence, ProviderQuotaExhausted, ProviderRateLimited, normalize_availability_type
from app.services.ott_providers import normalize_platform


class WatchmodeProvider:
    name = "watchmode"
    daily_limit = settings.WATCHMODE_DAILY_LIMIT
    monthly_limit = settings.WATCHMODE_MONTHLY_LIMIT

    @property
    def enabled(self):
        return settings.WATCHMODE_ENABLED

    @property
    def configured(self):
        return bool(self.enabled and settings.WATCHMODE_API_KEY)

    def fetch_movie(self, movie) -> list[NormalizedOttEvidence]:
        response = httpx.get(
            f"{settings.WATCHMODE_BASE_URL.rstrip('/')}/title/movie-{movie.tmdb_id}/sources/",
            params={"regions": "IN"},
            headers={"X-API-Key": settings.WATCHMODE_API_KEY, "Accept": "application/json"},
            timeout=20,
            follow_redirects=True,
        )
        if response.status_code == 429:
            raise ProviderRateLimited("Watchmode rate limit reached")
        if response.status_code in {402, 403}:
            raise ProviderQuotaExhausted("Watchmode quota or plan rejected the request")
        response.raise_for_status()
        now = datetime.now(timezone.utc)
        result = []
        payload = response.json()
        for item in payload if isinstance(payload, list) else []:
            if (item.get("region") or "IN").upper() != "IN":
                continue
            platform = normalize_platform(item.get("name"))
            if not platform:
                continue
            result.append(
                NormalizedOttEvidence(
                    source_type="WATCHMODE",
                    source_name="Watchmode",
                    fact_type="AVAILABILITY",
                    platform_candidate=platform,
                    availability_type=normalize_availability_type(item.get("type")),
                    source_url=item.get("web_url") or "https://api.watchmode.com/",
                    observed_at=now,
                    raw_external_id=str(item.get("source_id") or "") or None,
                    tmdb_id=movie.tmdb_id,
                    title=movie.title,
                    year=(movie.release_date.year if movie.release_date else None),
                    language=movie.original_language,
                    movie_match_confidence=100,
                    platform_confidence=75,
                    date_confidence=0,
                )
            )
        return result
