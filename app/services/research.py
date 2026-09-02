"""Unified, auditable manual and scheduled movie research orchestration."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.movie_metadata import MovieRating
from app.models.operations import MovieRequest, OttEvidence
from app.models.ott_availability import OttAvailability
from app.models.research import ResearchRun
from app.services.backfill import DataHealthService
from app.services.movie_metadata_service import MovieMetadataService
from app.services.movie_requests import MovieRequestUpdateEmailService
from app.services.notification_service import NotificationService
from app.services.ott.intelligence import OTTIntelligenceService
from app.services.ott.reconciliation import OTTReconciliationService
from app.services.ott.web_research import WebOttResearchService
from app.services.rating_provider import IMDbRatingRefreshService
from app.services.release_status import ReleaseStatusService
from app.services.tmdb.movie_service import TMDbMovieService


TRIGGER_TYPES = {
    "ADMIN_MANUAL",
    "ADMIN_REQUEST",
    "NEW_MOVIE_IMPORT",
    "ADMIN_RETRY",
    "API_RETRY",
    "DISCOVERY_IMPORT",
    "AUTOMATED_SCHEDULE",
    "MOVIE_REQUEST",
    "DISCOVERY_REGULAR",
    "DISCOVERY_DEEP",
    "RETRY",
}
SCOPES = {"full", "tmdb", "imdb", "ott", "web"}


class ResearchPipelineService:
    """Execute existing provider services while persisting query-to-decision history."""

    def __init__(self, db: Session):
        self.db = db

    def create_run(
        self,
        *,
        movie_id: int | None = None,
        request_id: str | None = None,
        trigger_type: str = "ADMIN_MANUAL",
        initiated_by: str = "admin",
        category: str = "FULL",
        parent_run_id: str | None = None,
        active_key: str | None = None,
    ) -> tuple[ResearchRun, bool]:
        trigger_type = trigger_type.upper()
        if trigger_type not in TRIGGER_TYPES:
            raise ValueError("Unsupported research trigger type")
        if movie_id is not None and not self.db.get(Movie, movie_id):
            raise LookupError("Movie not found")
        active_key = active_key or (f"movie:{movie_id}" if movie_id else None)
        if active_key:
            active = self.db.query(ResearchRun).filter_by(active_key=active_key).first()
            if active:
                return active, False
        run = ResearchRun(
            run_id=str(uuid4()),
            parent_run_id=parent_run_id,
            trigger_type=trigger_type,
            initiated_by=initiated_by[:100],
            category=category.upper()[:40],
            movie_id=movie_id,
            request_id=request_id,
            active_key=active_key,
            status="QUEUED",
            queries_attempted=[],
            providers_attempted=[],
            database_changes=[],
            notification_results={},
            errors=[],
            details={},
        )
        self.db.add(run)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            if active_key:
                active = self.db.query(ResearchRun).filter_by(active_key=active_key).first()
                if active:
                    return active, False
            raise
        self.db.refresh(run)
        return run, True

    def queue_preview(self, limit: int | None = None) -> dict:
        limit = max(1, min(limit or settings.OTT_DAILY_RESEARCH_MOVIE_LIMIT, 100))
        query = self.db.query(Movie).filter(Movie.ott_research_eligibility == "ELIGIBLE")
        eligible = query.count()
        movies = query.order_by(
            Movie.theatrical_release_date.desc().nullslast(),
            Movie.popularity.desc().nullslast(),
            Movie.id,
        ).limit(limit).all()
        return {
            "eligible_count": eligible,
            "batch_size": limit,
            "movies": [
                {"id": movie.id, "title": movie.title, "language": movie.original_language,
                 "release_date": (movie.theatrical_release_date or movie.release_date).isoformat() if (movie.theatrical_release_date or movie.release_date) else None}
                for movie in movies
            ],
        }

    def prepare_request(self, request_id: str) -> tuple[MovieRequest, Movie, bool]:
        """Resolve or safely import the exact TMDB identity attached to a request."""
        item = self.db.query(MovieRequest).filter_by(request_id=request_id).first()
        if not item:
            raise LookupError("Movie request not found")
        movie = self.db.get(Movie, item.local_movie_id) if item.local_movie_id else None
        movie = movie or self.db.query(Movie).filter_by(tmdb_id=item.external_movie_id).first()
        created = False
        if movie is None:
            payload = TMDbMovieService().get_rich_movie_details(item.external_movie_id)
            if payload.get("id") != item.external_movie_id or not (payload.get("title") or payload.get("original_title")):
                raise ValueError("TMDB identity verification failed")
            release_date = None
            try:
                release_date = date.fromisoformat(str(payload.get("release_date"))[:10]) if payload.get("release_date") else None
            except ValueError:
                pass
            movie = Movie(
                tmdb_id=item.external_movie_id,
                title=payload.get("title") or payload.get("original_title"),
                original_title=payload.get("original_title"),
                overview=payload.get("overview"),
                release_date=release_date,
                poster_path=payload.get("poster_path"),
                backdrop_path=payload.get("backdrop_path"),
                popularity=payload.get("popularity"),
                vote_average=payload.get("vote_average"),
                vote_count=payload.get("vote_count"),
                original_language=payload.get("original_language"),
                adult=bool(payload.get("adult", False)),
            )
            self.db.add(movie)
            try:
                self.db.flush()
                MovieMetadataService(self.db).enrich_movie(movie, payload=payload)
                ReleaseStatusService(self.db).classify_movie(movie)
                self.db.commit()
                created = True
            except IntegrityError:
                self.db.rollback()
                movie = self.db.query(Movie).filter_by(tmdb_id=item.external_movie_id).one()
        item = self.db.query(MovieRequest).filter_by(request_id=request_id).one()
        item.local_movie_id = movie.id
        matched_now = item.status == "PENDING"
        if matched_now:
            item.status = "REVIEWING"
        self.db.commit()
        if matched_now:
            MovieRequestUpdateEmailService(self.db).send(item, "MATCHED")
        return item, movie, created

    def create_request_run(self, request_id: str, initiated_by: str = "movie-request") -> tuple[ResearchRun, bool]:
        item, movie, created_movie = self.prepare_request(request_id)
        run, created = self.create_run(
            movie_id=movie.id,
            request_id=item.request_id,
            trigger_type="MOVIE_REQUEST" if initiated_by == "movie-request" else "ADMIN_MANUAL",
            initiated_by=initiated_by,
            category="FULL",
        )
        if created_movie:
            run.details = {"imported_movie": True}
            self.db.commit()
        return run, created

    def _snapshot(self, movie_id: int) -> dict:
        availability = self.db.query(OttAvailability).filter_by(movie_id=movie_id, country="IN").order_by(
            OttAvailability.locked_by_admin.desc(),
            OttAvailability.verification_status.desc(),
            OttAvailability.confidence.desc(),
            OttAvailability.id,
        ).first()
        rating = self.db.query(MovieRating).filter(
            MovieRating.movie_id == movie_id,
            func.lower(MovieRating.source) == "imdb",
        ).first()
        return {
            "platform": availability.provider if availability else None,
            "release_date": availability.ott_release_date if availability else None,
            "rating": rating.rating if rating else None,
            "confidence": availability.confidence if availability else None,
            "verification_status": availability.verification_status if availability else None,
        }

    def _save_error(self, run_id: str, step: str, exc: Exception) -> ResearchRun:
        self.db.rollback()
        run = self.db.query(ResearchRun).filter_by(run_id=run_id).one()
        run.errors = [*(run.errors or []), {"step": step, "error": sanitize_error(exc)}]
        self.db.commit()
        return run

    def fail_queued_run(self, run_id: str, step: str, exc: Exception) -> dict:
        """Release a persisted concurrency lock when a queued task cannot start."""
        self.db.rollback()
        run = self.db.query(ResearchRun).filter_by(run_id=run_id).first()
        if not run:
            raise LookupError("Research run not found")
        run.status = "FAILED"
        run.result = "FAILED"
        run.active_key = None
        run.completed_at = datetime.now(timezone.utc)
        run.errors = [*(run.errors or []), {"step": step, "error": sanitize_error(exc)}]
        self.db.commit()
        return self.serialize(run)

    def execute(self, run_id: str, scope: str = "full") -> dict:
        scope = scope.lower()
        if scope not in SCOPES:
            raise ValueError("Unsupported research scope")
        run = self.db.query(ResearchRun).filter_by(run_id=run_id).first()
        if not run:
            raise LookupError("Research run not found")
        if run.status == "COMPLETE":
            return self.serialize(run)
        movie = self.db.get(Movie, run.movie_id) if run.movie_id else None
        if not movie:
            run.status, run.result, run.active_key = "FAILED", "FAILED", None
            run.completed_at = datetime.now(timezone.utc)
            run.errors = [{"step": "input", "error": "Movie not found"}]
            self.db.commit()
            return self.serialize(run)

        before = self._snapshot(movie.id)
        evidence_before = self.db.query(func.count(OttEvidence.id)).filter_by(movie_id=movie.id).scalar() or 0
        run.status, run.started_at = "RUNNING", datetime.now(timezone.utc)
        run.before_platform = before["platform"]
        run.before_release_date = before["release_date"]
        run.before_imdb_rating = before["rating"]
        run.details = {"scope": scope, "steps": {}}
        self.db.commit()

        steps: dict[str, object] = {}
        providers: list[str] = []
        queries: list[str] = []

        if scope in {"full", "tmdb"}:
            providers.append("TMDB")
            try:
                MovieMetadataService(self.db).enrich_movie(movie)
                steps["tmdb"] = {"status": "COMPLETE"}
            except Exception as exc:
                self._save_error(run_id, "tmdb", exc)
                steps["tmdb"] = {"status": "FAILED", "error": sanitize_error(exc)}

        if scope in {"full", "imdb"}:
            providers.append("OMDb")
            try:
                steps["imdb"] = IMDbRatingRefreshService(self.db).refresh_movie(movie.id)
            except Exception as exc:
                self._save_error(run_id, "imdb", exc)
                steps["imdb"] = {"status": "FAILED", "error": sanitize_error(exc)}

        if scope in {"full", "ott"}:
            try:
                report = OTTIntelligenceService(self.db).refresh_movie(movie.id, research_run_id=run_id)
                steps["ott_providers"] = report
                providers.extend(report.get("providers", {}).keys())
            except Exception as exc:
                self._save_error(run_id, "ott_providers", exc)
                steps["ott_providers"] = {"status": "FAILED", "error": sanitize_error(exc)}

        if scope in {"full", "ott", "web"}:
            try:
                report = WebOttResearchService(self.db).research_movie(movie.id, research_run_id=run_id)
                steps["web"] = report
                providers.append(report.get("provider") or "WEB_SEARCH")
                queries.extend(report.get("queries") or [])
            except Exception as exc:
                self._save_error(run_id, "web", exc)
                steps["web"] = {"status": "FAILED", "error": sanitize_error(exc)}
            try:
                steps["reconciliation"] = {"status": OTTReconciliationService(self.db, settings.OTT_CONFIRMATION_THRESHOLD).reconcile(movie.id)}
                self.db.commit()
            except Exception as exc:
                self._save_error(run_id, "reconciliation", exc)
                steps["reconciliation"] = {"status": "FAILED", "error": sanitize_error(exc)}

        try:
            health = DataHealthService(self.db)
            refreshed = self.db.get(Movie, movie.id)
            health._set(movie.id, {
                "missing_poster": not refreshed.poster_path,
                "missing_backdrop": not refreshed.backdrop_path,
                "missing_imdb": not self.db.query(MovieRating.id).filter(MovieRating.movie_id == movie.id, func.lower(MovieRating.source) == "imdb").first(),
                "missing_ott_provider": not self.db.query(OttAvailability.id).filter_by(movie_id=movie.id).first(),
            })
            self.db.commit()
            steps["data_health"] = {"status": "COMPLETE"}
        except Exception as exc:
            self._save_error(run_id, "data_health", exc)
            steps["data_health"] = {"status": "FAILED", "error": sanitize_error(exc)}

        after = self._snapshot(movie.id)
        evidence_after = self.db.query(func.count(OttEvidence.id)).filter_by(movie_id=movie.id).scalar() or 0
        changes = [key for key in ("platform", "release_date", "rating", "verification_status") if before.get(key) != after.get(key)]
        reconciliation = (steps.get("reconciliation") or {}).get("status") if isinstance(steps.get("reconciliation"), dict) else None
        run = self.db.query(ResearchRun).filter_by(run_id=run_id).one()
        run.providers_attempted = list(dict.fromkeys(provider for provider in providers if provider))
        run.queries_attempted = list(dict.fromkeys(queries))
        run.web_searches_attempted = len(run.queries_attempted)
        run.sources_discovered = len((steps.get("web") or {}).get("sources", [])) if isinstance(steps.get("web"), dict) else 0
        run.evidence_created = max(0, evidence_after - evidence_before)
        run.after_platform = after["platform"]
        run.after_release_date = after["release_date"]
        run.after_imdb_rating = after["rating"]
        run.confidence = after["confidence"]
        run.database_changes = changes
        run.details = {"scope": scope, "steps": steps}
        if reconciliation == "CONFLICTING":
            run.result = "CONFLICTING"
        elif changes or run.evidence_created:
            run.result = "UPDATED"
        elif run.errors and all((value or {}).get("status") == "FAILED" for value in steps.values() if isinstance(value, dict)):
            run.result = "FAILED"
        elif isinstance(steps.get("web"), dict) and steps["web"].get("configured") and not steps["web"].get("results"):
            run.result = "NOT_FOUND"
        else:
            run.result = "NO_CHANGE"
        run.status = "FAILED" if run.result == "FAILED" else "COMPLETE"
        run.completed_at = datetime.now(timezone.utc)
        run.active_key = None
        self.db.commit()

        if changes and run.request_id:
            request = self.db.query(MovieRequest).filter_by(request_id=run.request_id).first()
            if request:
                message = (
                    f"Research update for request {request.request_id}: {movie.title}. "
                    f"OTT platform: {after['platform'] or 'not confirmed'}; "
                    f"OTT date: {after['release_date'] or 'not confirmed'}; "
                    f"IMDb rating: {after['rating'] if after['rating'] is not None else 'not available'}."
                )
                sent = NotificationService(self.db).notify(
                    message, "info", f"research-update:{run.run_id}", cooldown_minutes=10 * 365 * 24 * 60,
                    channels=("discord", "telegram"),
                )
                run = self.db.query(ResearchRun).filter_by(run_id=run_id).one()
                run.notification_results = {"admin_chat": "SENT" if sent else "NOT_CONFIGURED_OR_FAILED"}
                self.db.commit()
                if after["platform"]:
                    email_result = MovieRequestUpdateEmailService(self.db).send(request, "OTT_FOUND")
                    run = self.db.query(ResearchRun).filter_by(run_id=run_id).one()
                    run.notification_results = {**(run.notification_results or {}), "requester_ott_email": email_result["status"]}
                    self.db.commit()
        return self.serialize(self.db.query(ResearchRun).filter_by(run_id=run_id).one())

    def execute_queue(self, parent_run_id: str) -> dict:
        parent = self.db.query(ResearchRun).filter_by(run_id=parent_run_id).first()
        if not parent:
            raise LookupError("Research run not found")
        parent.status, parent.started_at = "RUNNING", datetime.now(timezone.utc)
        self.db.commit()
        preview = self.queue_preview()
        results = []
        for item in preview["movies"]:
            child, created = self.create_run(
                movie_id=item["id"], trigger_type=parent.trigger_type, initiated_by=parent.initiated_by,
                category="FULL", parent_run_id=parent.run_id,
            )
            results.append(self.execute(child.run_id) if created else self.serialize(child))
        parent = self.db.query(ResearchRun).filter_by(run_id=parent_run_id).one()
        parent.status = "COMPLETE"
        parent.result = "UPDATED" if any(item.get("result") == "UPDATED" for item in results) else "NO_CHANGE"
        parent.completed_at = datetime.now(timezone.utc)
        parent.active_key = None
        parent.details = {"preview": preview, "children": [item["run_id"] for item in results]}
        parent.evidence_created = sum(item.get("evidence_created") or 0 for item in results)
        self.db.commit()
        return self.serialize(parent)

    @staticmethod
    def serialize(run: ResearchRun) -> dict:
        def iso(value):
            return value.isoformat() if value else None
        return {
            "run_id": run.run_id,
            "parent_run_id": run.parent_run_id,
            "trigger_type": run.trigger_type,
            "initiated_by": run.initiated_by,
            "category": run.category,
            "status": run.status,
            "result": run.result,
            "movie_id": run.movie_id,
            "request_id": run.request_id,
            "started_at": iso(run.started_at),
            "completed_at": iso(run.completed_at),
            "created_at": iso(run.created_at),
            "queries_attempted": run.queries_attempted or [],
            "providers_attempted": run.providers_attempted or [],
            "web_searches_attempted": run.web_searches_attempted,
            "sources_discovered": run.sources_discovered,
            "evidence_created": run.evidence_created,
            "before": {"platform": run.before_platform, "release_date": iso(run.before_release_date), "imdb_rating": run.before_imdb_rating},
            "after": {"platform": run.after_platform, "release_date": iso(run.after_release_date), "imdb_rating": run.after_imdb_rating},
            "confidence": run.confidence,
            "database_changes": run.database_changes or [],
            "notification_results": run.notification_results or {},
            "errors": run.errors or [],
            "details": run.details or {},
        }
