"""Normalizer for inspected official platform/studio/distributor announcements."""

from datetime import datetime, timezone

from app.services.ott.providers.base import NormalizedOttEvidence, normalize_availability_type, normalize_date
from app.services.ott_providers import normalize_platform


class OfficialSourcesProvider:
    name = "official_sources"

    @staticmethod
    def normalize(raw: dict, *, source_type: str = "OFFICIAL_PLATFORM") -> NormalizedOttEvidence:
        confidence = 100 if source_type == "OFFICIAL_PLATFORM" else 95
        release_date = normalize_date(raw.get("release_date"))
        return NormalizedOttEvidence(
            source_type=source_type,
            source_name=raw.get("source_name") or "Official announcement",
            fact_type="ANNOUNCEMENT",
            platform_candidate=normalize_platform(raw.get("platform")),
            release_date_candidate=release_date,
            availability_type=normalize_availability_type(raw.get("availability_type") or "subscription"),
            source_url=raw.get("source_url"),
            source_published_at=normalize_date(raw.get("source_published_at")),
            observed_at=datetime.now(timezone.utc),
            tmdb_id=raw.get("tmdb_id"),
            imdb_id=raw.get("imdb_id"),
            title=raw.get("title"),
            year=raw.get("year"),
            language=raw.get("language"),
            platform_confidence=confidence,
            date_confidence=confidence,
            inspected=True,
        )
