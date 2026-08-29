"""Service orchestrating OTT availability retrieval, synchronization, and response summary formatting."""

from datetime import datetime, date, timezone
from sqlalchemy.orm import Session

from app.models.movie import Movie
from app.repositories.ott_availability_repository import OttAvailabilityRepository
from app.schemas.ott_availability import OttAvailabilitySummary, OttProviderItem
from app.services.google_search_service import GoogleSearchOttService
from app.services.tmdb.ott_service import TMDbOttService


class OttAvailabilityService:
    """Orchestrates TMDB Provider API synchronization, Google Search Fallback, and DB updates."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = OttAvailabilityRepository(db)
        self.tmdb_ott_service = TMDbOttService()
        self.google_search_service = GoogleSearchOttService()

    def sync_movie_ott(self, movie: Movie) -> OttAvailabilitySummary:
        """Fetch OTT availability from TMDB, execute Google Fallback if needed, update DB, and return summary."""
        now = datetime.now(timezone.utc)
        tmdb_providers = self.tmdb_ott_service.get_parsed_providers(movie.tmdb_id, region="IN")

        saved_records = []

        if tmdb_providers:
            for item in tmdb_providers:
                rec = self.repo.upsert_provider(
                    movie_id=movie.id,
                    provider=item["provider"],
                    country=item["country"],
                    watch_type=item["watch_type"],
                    provider_logo=item.get("provider_logo"),
                    source_type="TMDB",
                    source_url=item.get("watch_url"),
                    confidence=100.0,
                    last_checked=now,
                )
                saved_records.append(rec)
        else:
            # Fallback to Google Search when TMDB yields no availability data
            fallback_data = self.google_search_service.search_ott_release(movie)
            if fallback_data and fallback_data.get("confidence", 0.0) >= 90.0:
                rec = self.repo.upsert_provider(
                    movie_id=movie.id,
                    provider=fallback_data["provider"],
                    country=fallback_data["country"],
                    watch_type=fallback_data["watch_type"],
                    provider_logo=fallback_data.get("provider_logo"),
                    ott_release_date=fallback_data.get("ott_release_date"),
                    status=fallback_data.get("status", "available"),
                    source_type="GOOGLE_SEARCH",
                    source_url=fallback_data.get("source_url"),
                    confidence=fallback_data.get("confidence", 90.0),
                    last_checked=now,
                )
                saved_records.append(rec)

        self.repo.save()
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
