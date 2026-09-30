"""Resumable, catalogue-only TMDB movie backfill.

The importer deliberately does not persist watch-provider/OTT data. Progress is
stored in ``OperationState`` after every successful TMDB page so an interrupted
run resumes at the same language/year/page without duplicating movies.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.secrets import sanitize_error
from app.database.connection import SessionLocal
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId
from app.models.operations import OperationState
from app.services.languages import SUPPORTED_PRIMARY_LANGUAGE_CODES, normalize_language_code
from app.services.movie_metadata_service import MovieMetadataService
from app.services.release_status import ReleaseStatusService
from app.services.tmdb.movie_service import TMDbMovieService


SUPPORTED_LANGUAGES = list(SUPPORTED_PRIMARY_LANGUAGE_CODES)
BACKFILL_START_DATE = date(2011, 1, 1)
OPERATION_PREFIX = "movies.catalogue_backfill"

# Conservative relevance floors keep global catalogues useful without losing
# Indian regional and independent films, which deliberately have no vote floor.
MINIMUM_VOTE_COUNTS = {
    "en": 20,
    "ko": 5,
    "ja": 5,
    "zh": 5,
    "es": 5,
    "fr": 5,
}


class TMDbBulkImporter:
    """Import eligible movies language-by-language with durable checkpoints."""

    def __init__(
        self,
        languages: list[str] | None = None,
        start_year: int = BACKFILL_START_DATE.year,
        end_year: int | None = None,
        checkpoint_path: str | None = None,
        *,
        end_date: date | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
        tmdb_service: TMDbMovieService | None = None,
        operation_prefix: str = OPERATION_PREFIX,
        minimum_vote_counts: dict[str, int] | None = None,
        year_order: list[int] | None = None,
    ):
        # Retained for CLI compatibility. Durable progress now lives in the DB.
        del checkpoint_path
        normalized = [normalize_language_code(item) for item in (languages or SUPPORTED_LANGUAGES)]
        invalid = [item for item in normalized if item not in SUPPORTED_LANGUAGES]
        if invalid:
            raise ValueError(f"Unsupported language(s): {invalid}")
        self.languages = list(dict.fromkeys(normalized))
        self.start_year = max(start_year, BACKFILL_START_DATE.year)
        self.end_date = end_date or date.today()
        self.end_year = min(end_year or self.end_date.year, self.end_date.year)
        default_year_order = list(range(self.start_year, self.end_year + 1))
        self.year_order = list(dict.fromkeys(year_order or default_year_order))
        if not self.year_order or any(
            year < self.start_year or year > self.end_year for year in self.year_order
        ):
            raise ValueError("year_order must stay within the configured year range")
        self.session_factory = session_factory
        self.tmdb_service = tmdb_service or TMDbMovieService()
        self.operation_prefix = operation_prefix
        self.minimum_vote_counts = (
            dict(MINIMUM_VOTE_COUNTS)
            if minimum_vote_counts is None
            else dict(minimum_vote_counts)
        )
        self.stats = {
            "processed": 0,
            "inserted": 0,
            "existing": 0,
            "filtered": 0,
            "duplicate_imdb": 0,
            "errors": 0,
            "rejected_by_reason": {},
            "languages": {},
        }

    def _state(self, db: Session, language: str) -> OperationState:
        name = f"{self.operation_prefix}.{language}"
        state = db.query(OperationState).filter_by(name=name).first()
        if not state:
            state = OperationState(name=name, status="IDLE", details={})
            db.add(state)
            db.flush()
        return state

    def _initial_details(self, db: Session, language: str) -> dict:
        return {
            "language": language,
            "start_date": BACKFILL_START_DATE.isoformat(),
            "end_date": self.end_date.isoformat(),
            "current_year": self.year_order[0],
            "year_order": self.year_order,
            "year_index": 0,
            "page": 1,
            "completed_years": [],
            "before": db.query(Movie).filter(Movie.original_language == language).count(),
            "processed": 0,
            "inserted": 0,
            "existing": 0,
            "filtered": 0,
            "duplicate_imdb": 0,
            "errors": 0,
            "rejected_by_reason": {},
            "minimum_vote_count": self.minimum_vote_counts.get(language, 0),
        }

    @staticmethod
    def _checkpoint(
        db: Session,
        state: OperationState,
        details: dict,
        status: str = "RUNNING",
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        state.status = status
        state.cursor = int(details.get("page") or 1)
        state.processed_count = int(details.get("processed") or 0)
        state.details = dict(details)
        state.last_error = error
        if status == "COMPLETE":
            state.last_success_at = now
            state.completed_at = now
        elif status == "FAILED":
            state.last_failure_at = now
        db.commit()

    @staticmethod
    def _release_date(payload: dict) -> date | None:
        try:
            return date.fromisoformat(str(payload.get("release_date") or "")[:10])
        except ValueError:
            return None

    def _rejection_reason(
        self,
        details: dict,
        language: str,
        year: int,
        discover_item: dict | None = None,
    ) -> str | None:
        actual_language = normalize_language_code(details.get("original_language"))
        # TMDB can expose a primary date in Discover while /movie/{id} and
        # /release_dates still return no date. Preserve that provider date, and
        # allow an otherwise valid upcoming identity to be enriched later when
        # neither payload has an exact date yet.
        release_date = self._release_date(details) or self._release_date(
            discover_item or {}
        )
        runtime = details.get("runtime")
        if not details.get("id") or not (
            details.get("title") or details.get("original_title")
        ):
            return "invalid_metadata"
        if details.get("adult"):
            return "adult"
        if details.get("video"):
            return "other"
        if actual_language != language:
            return "wrong_language"
        if release_date is not None and (
            release_date.year != year
            or not BACKFILL_START_DATE <= release_date <= self.end_date
        ):
            return "other"
        if isinstance(runtime, int) and 0 < runtime < 40:
            return "short_runtime"

        if language == "en":
            # The zero-vote English feed contains a very large tail of test,
            # student, event, and incomplete records. Runtime zero/unknown is
            # accepted only for a real upcoming production with corroborating
            # metadata; released entries need feature-film evidence.
            credits = details.get("credits") or {}
            external_ids = details.get("external_ids") or {}
            evidence = sum(
                (
                    bool(details.get("poster_path") or (discover_item or {}).get("poster_path")),
                    len(str(details.get("overview") or "").strip()) >= 40,
                    bool(details.get("production_companies")),
                    len(credits.get("cast") or []) >= 3,
                    bool(external_ids.get("imdb_id")),
                )
            )
            status = str(details.get("status") or "").strip().casefold()
            upcoming_status = status in {
                "planned",
                "in production",
                "post production",
            }
            runtime_known = isinstance(runtime, int) and runtime >= 40
            if not runtime_known:
                if not upcoming_status or evidence < 3:
                    return "invalid_metadata"
            elif int(details.get("vote_count") or 0) == 0 and evidence < 2:
                return "invalid_metadata"
        return None

    def _eligible(
        self,
        details: dict,
        language: str,
        year: int,
        discover_item: dict | None = None,
    ) -> bool:
        return self._rejection_reason(
            details, language, year, discover_item
        ) is None

    def _process_item(self, db: Session, item: dict, language: str, year: int) -> str:
        tmdb_id = item.get("id")
        if not isinstance(tmdb_id, int):
            return "filtered:invalid_metadata"
        existing = db.query(Movie).filter_by(tmdb_id=tmdb_id).first()
        if existing:
            # Preserve identities, relationships, OTT data, and populated
            # metadata. Fill only cheap missing catalogue scalars from discover.
            if not existing.poster_path and item.get("poster_path"):
                existing.poster_path = item["poster_path"]
            if not existing.backdrop_path and item.get("backdrop_path"):
                existing.backdrop_path = item["backdrop_path"]
            if existing.popularity is None and item.get("popularity") is not None:
                existing.popularity = item["popularity"]
            db.commit()
            return "existing"

        details = self.tmdb_service.get_rich_movie_details(tmdb_id)
        rejection_reason = self._rejection_reason(details, language, year, item)
        if rejection_reason:
            return f"filtered:{rejection_reason}"
        imdb_id = str((details.get("external_ids") or {}).get("imdb_id") or "").strip()
        if imdb_id and (
            db.query(ExternalId.id)
            .filter(func.lower(ExternalId.provider) == "imdb", ExternalId.external_id == imdb_id)
            .first()
        ):
            return "duplicate_imdb"

        movie = Movie(
            tmdb_id=tmdb_id,
            title=str(details.get("title") or details.get("original_title")),
            original_title=details.get("original_title"),
            original_language=language,
            release_date=self._release_date(details) or self._release_date(item),
            adult=False,
        )
        db.add(movie)
        db.flush()
        MovieMetadataService(
            db,
            cache_artwork=False,
            include_ott=False,
        ).enrich_movie(movie, payload=details)
        ReleaseStatusService(db).classify_movie(movie)
        db.commit()
        return "inserted"

    def _run_language(
        self,
        language: str,
        *,
        reset_checkpoint: bool,
        restart_completed: bool,
        max_pages: int | None,
        max_new_movies: int | None,
    ) -> dict:
        db = self.session_factory()
        try:
            state = self._state(db, language)
            details = (
                self._initial_details(db, language)
                if reset_checkpoint or not state.details
                else dict(state.details)
            )
            stored_year_order = details.get("year_order")
            if stored_year_order is None:
                sequential = list(range(self.start_year, self.end_year + 1))
                if self.year_order != sequential:
                    details = self._initial_details(db, language)
                else:
                    details["year_order"] = sequential
                    details["year_index"] = max(
                        0,
                        int(details.get("current_year") or self.start_year)
                        - self.start_year,
                    )
            elif list(stored_year_order) != self.year_order:
                details = self._initial_details(db, language)
            year_index = int(details.get("year_index") or 0)
            completed = (
                state.status == "COMPLETE"
                and year_index >= len(self.year_order)
            )
            if completed and not reset_checkpoint and not restart_completed:
                return details
            if completed and restart_completed:
                details = self._initial_details(db, language)
                year_index = 0
            details["end_date"] = self.end_date.isoformat()
            pages_this_run = 0
            inserted_this_run = 0
            self._checkpoint(db, state, details)

            while year_index < len(self.year_order):
                year = self.year_order[year_index]
                details["current_year"] = year
                details["year_index"] = year_index
                page = int(details.get("page") or 1)
                try:
                    payload = self.tmdb_service.discover_movies_by_language_and_year(
                        language,
                        year,
                        page=page,
                        minimum_vote_count=self.minimum_vote_counts.get(language, 0),
                        release_date_lte=self.end_date if year == self.end_date.year else None,
                        sort_by="primary_release_date.asc",
                    )
                except Exception as exc:
                    db.rollback()
                    state = self._state(db, language)
                    self._checkpoint(db, state, details, "FAILED", sanitize_error(exc))
                    raise

                items = payload.get("results") or []
                total_pages = min(int(payload.get("total_pages") or 1), 500)
                for item in items:
                    details["processed"] += 1
                    self.stats["processed"] += 1
                    try:
                        result = self._process_item(db, item, language, year)
                    except Exception as exc:
                        db.rollback()
                        result = "errors"
                        details["last_item_error"] = sanitize_error(exc)
                    result_key, _, rejection_reason = result.partition(":")
                    details[result_key] = int(details.get(result_key) or 0) + 1
                    self.stats[result_key] += 1
                    if rejection_reason:
                        reasons = dict(details.get("rejected_by_reason") or {})
                        reasons[rejection_reason] = int(reasons.get(rejection_reason) or 0) + 1
                        details["rejected_by_reason"] = reasons
                        aggregate_reasons = self.stats["rejected_by_reason"]
                        aggregate_reasons[rejection_reason] = (
                            int(aggregate_reasons.get(rejection_reason) or 0) + 1
                        )
                    if result_key == "inserted":
                        inserted_this_run += 1
                    if max_new_movies and inserted_this_run >= max_new_movies:
                        details.update(current_year=year, page=page)
                        state = self._state(db, language)
                        self._checkpoint(db, state, details, "PAUSED")
                        return details

                pages_this_run += 1
                if not items or page >= total_pages:
                    completed = set(details.get("completed_years") or [])
                    completed.add(year)
                    details.update(
                        completed_years=sorted(completed),
                        year_index=year_index + 1,
                        page=1,
                    )
                    year_index += 1
                    details["current_year"] = (
                        self.year_order[year_index]
                        if year_index < len(self.year_order)
                        else self.end_year + 1
                    )
                else:
                    details["page"] = page + 1
                state = self._state(db, language)
                self._checkpoint(db, state, details)
                if max_pages and pages_this_run >= max_pages:
                    state = self._state(db, language)
                    self._checkpoint(db, state, details, "PAUSED")
                    return details

            details["after"] = db.query(Movie).filter(Movie.original_language == language).count()
            details["current_year"] = self.end_year + 1
            details["year_index"] = len(self.year_order)
            details["page"] = 1
            state = self._state(db, language)
            self._checkpoint(db, state, details, "COMPLETE")
            return details
        finally:
            db.close()

    def run_import(
        self,
        reset_checkpoint: bool = False,
        *,
        restart_completed: bool = False,
        max_pages: int | None = None,
        max_new_movies_per_language: int | None = None,
    ) -> dict:
        for language in self.languages:
            result = self._run_language(
                language,
                reset_checkpoint=reset_checkpoint,
                restart_completed=restart_completed,
                max_pages=max_pages,
                max_new_movies=max_new_movies_per_language,
            )
            self.stats["languages"][language] = result
        return self.stats
