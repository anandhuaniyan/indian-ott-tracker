"""Normalizer for inspected reputable entertainment news evidence."""

from datetime import datetime, timezone

from app.services.ott.providers.base import NormalizedOttEvidence, normalize_availability_type, normalize_date
from app.services.ott_providers import normalize_platform


class NewsSearchProvider:
    name = "news"

    @staticmethod
    def normalize(raw: dict, *, tier: str = "A") -> NormalizedOttEvidence:
        confidence = {"A": 80, "B": 70, "C": 20}.get(tier.upper(), 20)
        release_date = normalize_date(raw.get("release_date"))
        return NormalizedOttEvidence(
            source_type="NEWS",
            source_name=raw.get("source_name") or "Entertainment publication",
            fact_type="ANNOUNCEMENT" if release_date else "AVAILABILITY",
            platform_candidate=normalize_platform(raw.get("platform")),
            release_date_candidate=release_date,
            availability_type=normalize_availability_type(raw.get("availability_type") or "subscription"),
            source_url=raw.get("source_url"),
            source_published_at=normalize_date(raw.get("source_published_at")),
            observed_at=datetime.now(timezone.utc),
            title=raw.get("title"),
            year=raw.get("year"),
            language=raw.get("language"),
            platform_confidence=confidence,
            date_confidence=confidence if release_date else 0,
            inspected=bool(raw.get("inspected", True)),
        )
