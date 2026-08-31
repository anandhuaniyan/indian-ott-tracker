"""Manual evidence normalizer; manual facts are locked at reconciliation time."""

from datetime import datetime, timezone

from app.services.ott.providers.base import NormalizedOttEvidence, normalize_availability_type, normalize_date
from app.services.ott_providers import normalize_platform


class ManualProvider:
    name = "manual"

    @staticmethod
    def normalize(raw: dict) -> NormalizedOttEvidence:
        release_date = normalize_date(raw.get("release_date"))
        return NormalizedOttEvidence(
            source_type="MANUAL",
            source_name=raw.get("source_name") or "Administrator",
            fact_type="ANNOUNCEMENT" if release_date else "AVAILABILITY",
            platform_candidate=normalize_platform(raw.get("platform")),
            release_date_candidate=release_date,
            availability_type=normalize_availability_type(raw.get("availability_type") or "subscription"),
            source_url=raw.get("source_url"),
            observed_at=datetime.now(timezone.utc),
            tmdb_id=raw.get("tmdb_id"),
            imdb_id=raw.get("imdb_id"),
            movie_match_confidence=100,
            platform_confidence=100,
            date_confidence=100 if release_date else 0,
            verification_method="MANUAL",
        )
