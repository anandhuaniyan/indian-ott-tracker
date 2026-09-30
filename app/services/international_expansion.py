"""Controlled, resumable population of international movie originals.

This intentionally uses TMDB's movie endpoint only. India-market candidates
are processed first, followed by the most popular global originals needed to
make each language page useful. Only India watch-provider rows are persisted by
the metadata service; a dub never changes or duplicates a movie identity.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.core.secrets import sanitize_error
from app.models.discovery import MovieDiscoveryRun
from app.models.movie import Movie
from app.models.operations import OperationState
from app.services.languages import (
    ALL_PUBLIC_LANGUAGE_NAMES,
    INTERNATIONAL_LANGUAGE_CODES,
)
from app.services.movie_discovery import MovieDiscoveryService
from app.services.tmdb.movie_service import TMDbMovieService


INTERNATIONAL_TARGETS = {
    "en": 2000,
    "ko": 800,
    "ja": 800,
    "zh": 800,
    "es": 600,
    "fr": 600,
    "th": 500,
}
EXPANSION_YEARS = tuple(range(2026, 2019, -1))


class InternationalExpansionService:
    operation = "movies.international_expansion"

    def __init__(self, db: Session, tmdb: TMDbMovieService | None = None):
        self.db = db
        self.tmdb = tmdb or TMDbMovieService()
        # The expansion remains comfortably below TMDB's request ceiling; the
        # shorter delay avoids turning a bounded catalogue load into a multi-
        # hour operation. Retry/backoff handling remains in TMDbClient.
        if hasattr(self.tmdb, "client"):
            self.tmdb.client.request_delay = min(self.tmdb.client.request_delay, 0.10)
        self.discovery = MovieDiscoveryService(
            db,
            tmdb=self.tmdb,
            enqueue_imports=False,
            cache_artwork=False,
        )

    def _state(self, name: str | None = None) -> OperationState:
        state = self.db.query(OperationState).filter_by(name=name or self.operation).first()
        if not state:
            state = OperationState(name=name or self.operation, details={})
            self.db.add(state)
            self.db.flush()
        return state

    def _checkpoint(self, state: OperationState, details: dict) -> None:
        state.details = details
        state.processed_count = sum(
            item.get("processed", 0) for item in details["languages"].values()
        )
        self.db.commit()

    def run(self, targets: dict[str, int] | None = None) -> dict:
        targets = targets or INTERNATIONAL_TARGETS
        invalid = set(targets) - set(INTERNATIONAL_LANGUAGE_CODES)
        if invalid:
            raise ValueError(f"Unsupported international language(s): {sorted(invalid)}")

        now = datetime.now(timezone.utc)
        state_name = (
            f"{self.operation}.{next(iter(targets))}"
            if len(targets) == 1
            else self.operation
        )
        state = self._state(state_name)
        state.status = "RUNNING"
        state.total_count = sum(targets.values())
        state.last_error = None
        details = {
            "years": list(EXPANSION_YEARS),
            "market": "IN",
            "languages": {},
        }
        state.details = details
        self.db.commit()

        run = MovieDiscoveryRun(
            run_type="INTERNATIONAL",
            slot="AD_HOC",
            status="RUNNING",
            started_at=now,
            window_start=date(2020, 1, 1),
            window_end=date(2026, 12, 31),
            languages=list(targets),
            language_stats={},
            source_stats={"scope": "TMDB movie originals; India offers first"},
            trigger_type="ADMIN_MANUAL",
            initiated_by="scripts:international_expansion",
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        try:
            for language, target in targets.items():
                before = self.db.query(Movie).filter_by(original_language=language).count()
                stats = {
                    "language": ALL_PUBLIC_LANGUAGE_NAMES[language],
                    "target": target,
                    "before": before,
                    "processed": 0,
                    "imported": 0,
                    "existing": 0,
                    "needs_review": 0,
                    "filtered": 0,
                    "failed": 0,
                    "status": "RUNNING",
                }
                details["languages"][language] = stats
                if before >= target:
                    stats.update(after=before, status="COMPLETE")
                    self._checkpoint(state, details)
                    continue

                # India-offer results first; the global pass fills a bounded,
                # popularity-ranked catalogue instead of mirroring TMDB.
                for india_only in (True, False):
                    if self.db.query(Movie).filter_by(original_language=language).count() >= target:
                        break
                    for year in EXPANSION_YEARS:
                        if self.db.query(Movie).filter_by(original_language=language).count() >= target:
                            break
                        page = 1
                        total_pages = 1
                        while page <= min(total_pages, 500):
                            payload = self.tmdb.discover_movies_by_language_and_year(
                                language,
                                year,
                                page,
                                watch_region="IN" if india_only else None,
                                sort_by="popularity.desc",
                            )
                            total_pages = int(payload.get("total_pages") or 1)
                            items = payload.get("results") or []
                            if not items:
                                break
                            for item in items:
                                if self.db.query(Movie).filter_by(original_language=language).count() >= target:
                                    break
                                if not isinstance(item.get("id"), int):
                                    continue
                                result = self.discovery._process_tmdb_candidate(
                                    run.id, language, item
                                )
                                stats["processed"] += 1
                                key = {
                                    "IMPORTED": "imported",
                                    "EXISTING": "existing",
                                    "NEEDS_REVIEW": "needs_review",
                                    "FILTERED": "filtered",
                                    "FAILED": "failed",
                                }.get(result)
                                if key:
                                    stats[key] += 1
                            stats["phase"] = "INDIA_FIRST" if india_only else "POPULAR_GLOBAL"
                            stats["year"] = year
                            stats["page"] = page
                            stats["after"] = self.db.query(Movie).filter_by(
                                original_language=language
                            ).count()
                            self._checkpoint(state, details)
                            page += 1
                stats["after"] = self.db.query(Movie).filter_by(
                    original_language=language
                ).count()
                stats["status"] = "COMPLETE" if stats["after"] >= target else "EXHAUSTED"
                stats.pop("phase", None)
                stats.pop("year", None)
                stats.pop("page", None)
                self._checkpoint(state, details)

            run.language_stats = details["languages"]
            run.completed_at = datetime.now(timezone.utc)
            run.status = "COMPLETE"
            run.candidates_discovered = sum(
                item["processed"] for item in details["languages"].values()
            )
            run.already_existing = sum(
                item["existing"] for item in details["languages"].values()
            )
            run.new_movies_imported = sum(
                item["imported"] for item in details["languages"].values()
            )
            run.needs_review = sum(
                item["needs_review"] for item in details["languages"].values()
            )
            run.failed = sum(item["failed"] for item in details["languages"].values())
            state.status = "COMPLETE"
            state.last_success_at = run.completed_at
            state.completed_at = run.completed_at
            self.db.commit()
            return details
        except Exception as exc:
            self.db.rollback()
            state = self._state(state_name)
            state.status = "FAILED"
            state.last_failure_at = datetime.now(timezone.utc)
            state.last_error = sanitize_error(exc)
            run = self.db.get(MovieDiscoveryRun, run.id)
            run.status = "FAILED"
            run.completed_at = state.last_failure_at
            run.last_error = state.last_error
            self.db.commit()
            raise
