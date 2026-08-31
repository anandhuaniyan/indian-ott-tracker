"""TMDB/JustWatch India availability and separate digital-date evidence."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config.settings import settings
from app.services.ott.providers.base import NormalizedOttEvidence, normalize_availability_type
from app.services.ott_providers import normalize_platform
from app.services.tmdb.client import TMDbClient


class TMDBOTTProvider:
    name = "tmdb_justwatch"
    daily_limit = settings.TMDB_OTT_DAILY_LIMIT
    monthly_limit = 0

    def __init__(self, client=None):
        self.client = client or TMDbClient()

    @property
    def enabled(self):
        return bool(settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN)

    @property
    def configured(self):
        return self.enabled

    def fetch_movie(self, movie) -> list[NormalizedOttEvidence]:
        now = datetime.now(timezone.utc)
        evidence: list[NormalizedOttEvidence] = []
        providers = self.client.get(f"/movie/{movie.tmdb_id}/watch/providers").get("results", {}).get("IN", {})
        source_url = providers.get("link") or f"https://www.themoviedb.org/movie/{movie.tmdb_id}/watch"
        for category in ("flatrate", "free", "ads", "rent", "buy"):
            for item in providers.get(category, []):
                platform = normalize_platform(item.get("provider_name"))
                if not platform:
                    continue
                evidence.append(
                    NormalizedOttEvidence(
                        source_type="JUSTWATCH_TMDB",
                        source_name="TMDB watch providers (JustWatch)",
                        fact_type="AVAILABILITY",
                        platform_candidate=platform,
                        availability_type=normalize_availability_type(category),
                        source_url=source_url,
                        observed_at=now,
                        raw_external_id=str(item.get("provider_id") or "") or None,
                        tmdb_id=movie.tmdb_id,
                        title=movie.title,
                        original_title=movie.original_title,
                        year=(movie.release_date.year if movie.release_date else None),
                        language=movie.original_language,
                        movie_match_confidence=100,
                        platform_confidence=75,
                        date_confidence=0,
                        notes="Availability only; release date is not inferred",
                    )
                )
        release_payload = self.client.get(f"/movie/{movie.tmdb_id}/release_dates")
        for country in release_payload.get("results", []):
            if country.get("iso_3166_1") != "IN":
                continue
            for item in country.get("release_dates", []):
                if int(item.get("type") or 0) != 4 or not item.get("release_date"):
                    continue
                try:
                    digital_date = datetime.fromisoformat(item["release_date"].replace("Z", "+00:00")).date()
                except (TypeError, ValueError):
                    continue
                evidence.append(
                    NormalizedOttEvidence(
                        source_type="TMDB",
                        source_name="TMDB release dates",
                        fact_type="DIGITAL_DATE",
                        release_date_candidate=digital_date,
                        availability_type="UNKNOWN",
                        source_url=f"https://www.themoviedb.org/movie/{movie.tmdb_id}/releases",
                        observed_at=now,
                        raw_external_id=f"movie/{movie.tmdb_id}/release_dates:IN:4",
                        tmdb_id=movie.tmdb_id,
                        title=movie.title,
                        year=(movie.release_date.year if movie.release_date else None),
                        language=movie.original_language,
                        movie_match_confidence=100,
                        date_confidence=35,
                        notes="TMDB type 4 digital evidence; never automatic subscription OTT date",
                    )
                )
        return evidence
