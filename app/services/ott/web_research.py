"""Bounded, evidence-first web research for unresolved Indian movie OTT facts.

This is intentionally a runner, not a scraper: it uses a configured lawful
search API, fetches only allow-listed supporting pages, and never turns a
search miss or a transport failure into an OTT ``NOT_FOUND`` claim.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.operations import MovieRequest, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.operations import OttResearchService
from app.services.ott_providers import configured_ott_provider, inspect_source


LANGUAGES = ("ml", "ta", "te", "hi", "kn")


class WebOttResearchService:
    """Process a checkpointed 30--60 day queue with conservative publication."""

    operation = "ott.web_research_one_shot"

    def __init__(self, db: Session, provider=None):
        self.db = db
        self.provider = provider or configured_ott_provider()

    def _state(self) -> OperationState:
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if not state:
            state = OperationState(name=self.operation, details={})
            self.db.add(state)
            self.db.flush()
        return state

    def targets(self, limit: int = 50):
        today = date.today()
        lower, upper = today - timedelta(days=60), today - timedelta(days=30)
        # Include requests first, then all target-language releases with no
        # confirmed India platform/date.  Admin locks are excluded entirely.
        requested = self.db.query(MovieRequest.local_movie_id).filter(
            MovieRequest.local_movie_id.is_not(None)
        )
        rows = (
            self.db.query(Movie)
            .outerjoin(OttAvailability, OttAvailability.movie_id == Movie.id)
            .filter(
                Movie.original_language.in_(LANGUAGES),
                or_(Movie.id.in_(requested), func.coalesce(Movie.theatrical_release_date, Movie.release_date).between(lower, upper)),
            )
            .group_by(Movie.id)
            .having(
                (func.count(OttAvailability.id) == 0)
                | (
                    (func.sum(case((OttAvailability.locked_by_admin.is_(True), 1), else_=0)) == 0)
                    & (func.sum(case((OttAvailability.verification_status == "CONFIRMED", 1), else_=0)) == 0)
                )
            )
            .order_by(Movie.popularity.desc().nullslast(), Movie.id)
            .limit(limit)
            .all()
        )
        return rows

    def _save_result(self, movie: Movie, result: dict, research_run_id: str | None = None) -> int:
        """Inspect, identity-check and persist one source; returns evidence count."""
        inspected = inspect_source(movie, result)
        if not inspected or inspected.get("country") != "IN":
            return 0
        platform = inspected.get("platform")
        release_date = inspected.get("release_date")
        if isinstance(release_date, str):
            try:
                release_date = date.fromisoformat(release_date[:10])
            except ValueError:
                release_date = None
        if not platform:
            return 0
        # A date needs a high-authority source (or later independent agreement).
        confidence = float(inspected.get("confidence") or 0)
        date_confidence = confidence if release_date and confidence >= 95 else 0
        evidence = OttResearchService(self.db, settings.OTT_CONFIRMATION_THRESHOLD).record_evidence(
            movie.id,
            platform=platform,
            release_date=release_date if date_confidence >= 95 else None,
            source_url=inspected["url"],
            source_title=inspected.get("title"),
            source_published_at=inspected.get("published_date"),
            source_name=inspected.get("source_name"),
            source_type=inspected.get("source_type"),
            country="IN",
            inspected=True,
            confidence=confidence,
            platform_confidence=confidence,
            date_confidence=date_confidence,
            movie_match_confidence=100,
            verification_method="WEB_RESEARCH",
            availability_type="SUBSCRIPTION",
            summary=(inspected.get("evidence_summary") or "")[:1000],
            allow_publication=True,
            research_run_id=research_run_id,
        )
        return int(bool(evidence))

    def research_movie(self, movie_id: int, research_run_id: str | None = None, max_queries: int = 3) -> dict:
        """Research one selected movie and return auditable query/source facts."""
        movie = self.db.get(Movie, movie_id)
        if not movie:
            raise LookupError("Movie not found")
        report = {
            "movie_id": movie_id,
            "provider": type(self.provider).__name__,
            "configured": bool(getattr(self.provider, "configured", False)),
            "queries": [],
            "sources": [],
            "results": 0,
            "evidence_created": 0,
        }
        if not report["configured"]:
            return report | {"status": "NOT_CONFIGURED"}
        results = self.provider.search(movie, max_queries=max_queries)
        report["results"] = len(results)
        for result in results[:8]:
            query = result.get("query")
            if query and query not in report["queries"]:
                report["queries"].append(query)
            url = result.get("url")
            if url:
                report["sources"].append({"url": url, "title": result.get("title"), "source": result.get("source_name")})
            report["evidence_created"] += self._save_result(movie, result, research_run_id)
        report["status"] = "COMPLETE"
        return report

    def run(self, limit: int = 30, research_run_id: str | None = None) -> dict:
        state, now = self._state(), datetime.now(timezone.utc)
        before = self.targets(limit=500)
        report = {"operation": self.operation, "target_movies": min(len(before), limit), "researched": 0, "evidence_created": 0, "technical_failures": 0, "not_found": 0, "platform_only": 0, "date_confirmed": 0, "skipped_locked": 0}
        if not getattr(self.provider, "configured", False):
            state.status, state.last_error = "BLOCKED", "A configured lawful web-search provider is required"
            state.details = report
            self.db.commit()
            return report | {"configured": False, "complete": False}
        state.status, state.last_error = "RUNNING", None
        self.db.commit()
        for movie in before[:limit]:
            try:
                results = self.provider.search(movie, max_queries=3)
                report["researched"] += 1
                for result in results[:8]:
                    report["evidence_created"] += self._save_result(movie, result, research_run_id)
                canonical = self.db.query(OttAvailability).filter_by(movie_id=movie.id, country="IN").first()
                if canonical and canonical.ott_release_date and canonical.verification_status == "CONFIRMED": report["date_confirmed"] += 1
                elif canonical and canonical.provider: report["platform_only"] += 1
                elif not results: report["not_found"] += 1
                state.cursor, state.processed_count = movie.id, state.processed_count + 1
                self.db.commit()
            except Exception as exc:
                self.db.rollback(); report["technical_failures"] += 1
                state = self._state(); state.last_error = sanitize_error(exc); state.cursor = movie.id; self.db.commit()
        state = self._state(); state.status, state.last_success_at, state.details = "COMPLETE", now, report
        self.db.commit()
        return report | {"configured": True, "complete": True, "manual_queue_after": len(self.targets(limit=500))}
