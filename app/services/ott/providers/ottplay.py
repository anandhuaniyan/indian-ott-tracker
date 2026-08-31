"""Normalization for permitted OTTplay discovery-adapter records."""

from datetime import datetime, timezone

from app.services.ott.providers.base import NormalizedOttEvidence, normalize_availability_type
from app.services.ott_providers import normalize_platform


class OTTPlayProvider:
    name = "ottplay"

    @staticmethod
    def normalize(raw: dict) -> NormalizedOttEvidence:
        value = raw.get("ott_release_date") or raw.get("release_date") or raw.get("date")
        release_date = None
        if value:
            try:
                release_date = datetime.fromisoformat(str(value)[:10]).date()
            except ValueError:
                pass
        return NormalizedOttEvidence(
            source_type="OTTPLAY",
            source_name="OTTplay",
            fact_type="RELEASE_DATE" if release_date else "AVAILABILITY",
            platform_candidate=normalize_platform(raw.get("platform") or raw.get("provider")),
            release_date_candidate=release_date,
            availability_type=normalize_availability_type(raw.get("availability_type") or "subscription"),
            source_url=raw.get("source_url") or raw.get("url"),
            observed_at=datetime.now(timezone.utc),
            raw_external_id=str(raw.get("id") or raw.get("external_id") or "") or None,
            tmdb_id=raw.get("tmdb_id"),
            imdb_id=raw.get("imdb_id"),
            title=raw.get("title") or raw.get("movie_title"),
            original_title=raw.get("original_title"),
            year=raw.get("year"),
            language=raw.get("language") or raw.get("original_language"),
            platform_confidence=80,
            date_confidence=80 if release_date else 0,
        )
