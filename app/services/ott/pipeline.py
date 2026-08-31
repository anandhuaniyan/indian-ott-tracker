"""Resumable, phased OTT intelligence collection workflows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.operations import MovieRequest, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.models.ott_intelligence import OttAvailabilityObservation
from app.services.ott.intelligence import OTTIntelligenceService
from app.services.ott.reconciliation import OTTReconciliationService


class OTTIntelligencePipeline:
    def __init__(self, db: Session):
        self.db = db
        self.intelligence = OTTIntelligenceService(db)

    def _state(self, name: str):
        state = self.db.query(OperationState).filter_by(name=name).first()
        if not state:
            state = OperationState(name=name, details={})
            self.db.add(state)
            self.db.flush()
        return state

    def _daily_movies(self, limit: int):
        now = datetime.now(timezone.utc)
        today = now.date()
        requested = exists(select(MovieRequest.id).where(MovieRequest.local_movie_id == Movie.id, MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"])))
        upcoming = exists(select(OttAvailability.id).where(OttAvailability.movie_id == Movie.id, OttAvailability.verification_status == "CONFIRMED", OttAvailability.ott_release_date.between(today - timedelta(days=1), today + timedelta(days=1))))
        last_observed = select(func.max(OttAvailabilityObservation.observed_at)).where(OttAvailabilityObservation.movie_id == Movie.id).correlate(Movie).scalar_subquery()
        return (
            self.db.query(Movie)
            .filter(
                or_(requested, upcoming, Movie.theatrical_release_date >= today - timedelta(days=365)),
                or_(last_observed.is_(None), last_observed < now - timedelta(hours=12)),
            )
            .order_by(
                case((requested, 0), (upcoming, 1), (Movie.theatrical_release_date >= today - timedelta(days=90), 2), (Movie.theatrical_release_date >= today - timedelta(days=180), 4), else_=6),
                Movie.popularity.desc().nullslast(),
                Movie.id.desc(),
            )
            .limit(limit)
            .all()
        )

    def run_daily(self, limit: int | None = None):
        limit = limit or settings.OTT_OBSERVATION_BATCH_SIZE
        state = self._state("ott.intelligence.daily")
        state.status = "RUNNING"
        self.db.commit()
        movies = self._daily_movies(limit)
        stats = {"processed": 0, "evidence": 0, "failures": 0, "providers": {}}
        for movie in movies:
            try:
                result = self.intelligence.refresh_movie(movie.id)
                stats["processed"] += 1
                stats["evidence"] += result["evidence"]
                for provider, value in result["providers"].items():
                    stats["providers"].setdefault(provider, {"calls": 0, "evidence": 0, "states": {}})
                    bucket = stats["providers"][provider]
                    bucket["calls"] += 1
                    bucket["evidence"] += value.get("evidence", 0)
                    bucket["states"][value["status"]] = bucket["states"].get(value["status"], 0) + 1
            except Exception as exc:
                stats["failures"] += 1
                state.last_error = sanitize_error(exc)
        state.status = "COMPLETE" if not stats["failures"] else "DEGRADED"
        state.processed_count += stats["processed"]
        state.last_success_at = datetime.now(timezone.utc)
        state.details = {"last_run": datetime.now(timezone.utc).isoformat(), "next_run": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "stats": stats}
        self.db.commit()
        return stats

    def run_weekly(self, limit: int | None = None):
        limit = limit or settings.OTT_OBSERVATION_BATCH_SIZE
        state = self._state("ott.intelligence.weekly")
        state.status = "RUNNING"
        self.db.commit()
        platform_only = exists(select(OttAvailability.id).where(OttAvailability.movie_id == Movie.id, OttAvailability.country == "IN", OttAvailability.ott_release_date.is_(None)))
        conflict = exists(select(OttEvidence.id).where(OttEvidence.movie_id == Movie.id, OttEvidence.status.in_(["CONFLICTING", "NEEDS_REVIEW"])))
        rows = self.db.query(Movie).filter(or_(platform_only, conflict)).order_by(Movie.popularity.desc().nullslast(), Movie.id.desc()).limit(limit).all()
        failures = 0
        for movie in rows:
            try:
                self.intelligence.refresh_movie(movie.id)
                OTTReconciliationService(self.db, settings.OTT_CONFIRMATION_THRESHOLD).reconcile(movie.id)
                self.db.commit()
            except Exception as exc:
                failures += 1
                state.last_error = sanitize_error(exc)
        state.status = "COMPLETE" if not failures else "DEGRADED"
        state.processed_count += len(rows)
        state.last_success_at = datetime.now(timezone.utc)
        state.details = {"processed": len(rows), "failures": failures, "next_run": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()}
        self.db.commit()
        return state.details
