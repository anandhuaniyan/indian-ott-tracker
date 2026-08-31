"""Cheapest-first OTT collection, observation, and reconciliation orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, selectinload

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId
from app.models.ott_intelligence import OttAvailabilityObservation
from app.services.operations import OttResearchService
from app.services.ott.matching import MovieMatchService
from app.services.ott.provider_controls import OttProviderCacheService, OttProviderControlService
from app.services.ott.providers import StreamingAvailabilityProvider, TMDBOTTProvider, WatchmodeProvider
from app.services.ott.providers.base import NormalizedOttEvidence, ProviderDisabled, ProviderError


class OTTIntelligenceService:
    """Collect independent facts without converting technical failure into NOT_FOUND."""

    def __init__(self, db: Session):
        self.db = db
        self.controls = OttProviderControlService(db)
        self.cache = OttProviderCacheService(db)
        self.matcher = MovieMatchService(db)

    @staticmethod
    def providers():
        return [TMDBOTTProvider(), StreamingAvailabilityProvider(), WatchmodeProvider()]

    @staticmethod
    def _ttl(movie: Movie) -> timedelta:
        release_date = movie.theatrical_release_date or movie.release_date
        if not release_date:
            return timedelta(days=settings.OTT_CACHE_HISTORICAL_DAYS)
        age = (datetime.now(timezone.utc).date() - release_date).days
        if age < 0:
            return timedelta(hours=settings.OTT_CACHE_UPCOMING_HOURS)
        if age <= 180:
            return timedelta(hours=settings.OTT_CACHE_RECENT_HOURS)
        return timedelta(days=settings.OTT_CACHE_HISTORICAL_DAYS)

    def _movie(self, movie_id: int):
        return (
            self.db.query(Movie)
            .options(selectinload(Movie.external_ids))
            .filter_by(id=movie_id)
            .first()
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        """Normalize database-returned timestamps before Python comparisons.

        SQLite drops timezone information even for timezone-aware columns while
        PostgreSQL preserves it.  Keeping the normalization here makes the
        observation coalescing path deterministic in tests and production.
        """
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _observe(self, movie_id: int, item: NormalizedOttEvidence, evidence_id: int | None):
        if item.fact_type != "AVAILABILITY":
            return
        observed_at = item.observed_at or datetime.now(timezone.utc)
        day_start = observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
        existing = self.db.query(OttAvailabilityObservation).filter(
            OttAvailabilityObservation.movie_id == movie_id,
            OttAvailabilityObservation.source_type == item.source_type,
            OttAvailabilityObservation.provider == item.platform_candidate,
            OttAvailabilityObservation.country == item.country.upper(),
            OttAvailabilityObservation.availability_type == item.availability_type,
            OttAvailabilityObservation.observed_at >= day_start,
            OttAvailabilityObservation.observed_at < day_start + timedelta(days=1),
        ).first()
        if existing:
            existing.observed_at = max(self._aware(existing.observed_at), self._aware(observed_at))
            existing.available = True
            existing.evidence_id = evidence_id or existing.evidence_id
            return
        self.db.add(
            OttAvailabilityObservation(
                movie_id=movie_id,
                provider=item.platform_candidate,
                country=item.country.upper(),
                availability_type=item.availability_type,
                available=True,
                source_type=item.source_type,
                source_url=item.source_url,
                raw_external_id=item.raw_external_id,
                observed_at=observed_at,
                evidence_id=evidence_id,
                details={"note": item.notes} if item.notes else None,
            )
        )

    def _observe_absent(self, movie_id: int, provider: str):
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        exists = self.db.query(OttAvailabilityObservation.id).filter(
            OttAvailabilityObservation.movie_id == movie_id,
            OttAvailabilityObservation.source_type == provider.upper(),
            OttAvailabilityObservation.available.is_(False),
            OttAvailabilityObservation.observed_at >= day_start,
        ).first()
        if not exists:
            self.db.add(
                OttAvailabilityObservation(
                    movie_id=movie_id,
                    provider=None,
                    country="IN",
                    availability_type="UNKNOWN",
                    available=False,
                    source_type=provider.upper(),
                    observed_at=now,
                    details={"meaning": "No India availability returned; not an OTT NOT_FOUND conclusion"},
                )
            )
            self.db.commit()

    def _persist(self, movie: Movie, item: NormalizedOttEvidence):
        match = self.matcher.match(item)
        if match.status != "MATCHED" or not match.movie or match.movie.id != movie.id:
            return None
        item.movie_match_confidence = max(item.movie_match_confidence, match.confidence)
        service = OttResearchService(self.db, settings.OTT_CONFIRMATION_THRESHOLD)
        evidence = service.record_evidence(
            movie.id,
            platform=item.platform_candidate,
            release_date=item.release_date_candidate,
            source_url=item.source_url,
            source_name=item.source_name,
            source_published_at=item.source_published_at,
            confidence=max(item.platform_confidence, item.date_confidence),
            summary=item.notes,
            source_type=item.source_type,
            country=item.country,
            inspected=item.inspected,
            fact_type=item.fact_type,
            availability_type=item.availability_type,
            raw_external_id=item.raw_external_id,
            movie_match_confidence=item.movie_match_confidence,
            platform_confidence=item.platform_confidence,
            date_confidence=item.date_confidence,
            verification_method=item.verification_method,
            observed_at=item.observed_at,
            allow_publication=settings.OTT_INTELLIGENCE_AUTO_PUBLICATION_ENABLED,
        )
        self._observe(movie.id, item, evidence.id)
        self.db.commit()
        return evidence

    def refresh_movie(self, movie_id: int, provider_names: set[str] | None = None) -> dict:
        movie = self._movie(movie_id)
        if not movie:
            raise LookupError("Movie not found")
        report = {"movie_id": movie_id, "providers": {}, "evidence": 0}
        for provider in self.providers():
            if provider_names and provider.name not in provider_names:
                continue
            key = f"movie:{movie.tmdb_id}:IN"
            cached = self.cache.get(provider.name, key)
            try:
                if cached is not None:
                    items = [NormalizedOttEvidence.from_serializable(item) for item in cached.get("items", [])]
                    source = "CACHE"
                else:
                    items = self.controls.execute(provider, lambda provider=provider: provider.fetch_movie(movie))
                    self.cache.put(provider.name, key, {"items": [item.serializable() for item in items]}, self._ttl(movie))
                    source = "LIVE"
            except ProviderDisabled:
                report["providers"][provider.name] = {"status": "DISABLED", "evidence": 0}
                continue
            except ProviderError as exc:
                report["providers"][provider.name] = {"status": getattr(exc, "status", "DOWN"), "error": sanitize_error(exc), "evidence": 0}
                continue
            except Exception as exc:
                report["providers"][provider.name] = {"status": "DOWN", "error": sanitize_error(exc), "evidence": 0}
                continue
            saved = sum(1 for item in items if self._persist(movie, item))
            if not items:
                self._observe_absent(movie.id, provider.name)
            report["providers"][provider.name] = {"status": "HEALTHY", "source": source, "evidence": saved}
            report["evidence"] += saved
        return report
