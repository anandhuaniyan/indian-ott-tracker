"""Service orchestrating OTT availability retrieval, synchronization, and response summary formatting."""

from datetime import datetime, date, timezone
from sqlalchemy.orm import Session

from app.models.movie import Movie
from app.repositories.ott_availability_repository import OttAvailabilityRepository
from app.schemas.ott_availability import OttAvailabilitySummary, OttProviderItem
from app.services.ott.intelligence import OTTIntelligenceService


class OttAvailabilityService:
    """Compatibility facade over the evidence-first OTT intelligence engine."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = OttAvailabilityRepository(db)

    def sync_movie_ott(self, movie: Movie) -> OttAvailabilitySummary:
        """Collect India evidence without publishing a search-derived date.

        Search snippets are no longer a write-through fallback. New-provider
        publication remains behind the manually verified gold-set accuracy gate.
        """
        OTTIntelligenceService(self.db).refresh_movie(movie.id, {"tmdb_justwatch"})
        return self.get_summary(movie.id)

    def get_summary(self, movie_id: int) -> OttAvailabilitySummary:
        """Build and return an OttAvailabilitySummary schema for a given movie."""
        records = self.repo.get_by_movie_id(movie_id)
        if not records:
            return OttAvailabilitySummary(
                available=False,
                ott_release_date=None,
                last_checked=None,
                providers=[],
            )

        providers = []
        earliest_release_date: date | None = None
        latest_last_checked: datetime | None = None

        for rec in records:
            providers.append(
                OttProviderItem(
                    name=rec.provider,
                    country=rec.country,
                    watch_type=rec.watch_type,
                    source=rec.source_type,
                    provider_logo=rec.provider_logo,
                    source_url=rec.source_url,
                )
            )

            if rec.ott_release_date and rec.verification_status == "CONFIRMED":
                if earliest_release_date is None or rec.ott_release_date < earliest_release_date:
                    earliest_release_date = rec.ott_release_date

            if rec.last_checked:
                if latest_last_checked is None or rec.last_checked > latest_last_checked:
                    latest_last_checked = rec.last_checked

        is_available = len(providers) > 0

        return OttAvailabilitySummary(
            available=is_available,
            ott_release_date=earliest_release_date,
            last_checked=latest_last_checked,
            providers=providers,
        )
