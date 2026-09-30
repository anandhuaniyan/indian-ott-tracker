"""Checkpointed IMDb rating enrichment using IMDb's official ratings dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
from pathlib import Path
import re
import time

from sqlalchemy import String, case, cast, exists, extract, func, or_
from sqlalchemy.orm import Session

from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieRating
from app.models.operations import BackfillRecord, OperationState
from app.models.ott_availability import OttAvailability
from app.services.rating_provider import (
    RATING_AVAILABLE,
    RATING_NOT_YET_RATED,
    ProviderQuotaExhausted,
    ProviderRateLimited,
    ProviderUnavailable,
    configured_rating_provider,
)
from app.services.tmdb.client import TMDbRequestError, metadata_request_context
from app.services.tmdb.movie_service import TMDbMovieService


OPERATION = "operations.imdb_rating_enrichment"
IMDB_DATASET_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
IMDB_ID = re.compile(r"^tt\d{7,10}$")
LANGUAGES = ("ml", "ta", "te", "hi", "kn", "en", "ko", "ja")
RECENT_YEARS = tuple(range(2026, 2019, -1))


@dataclass(frozen=True)
class DatasetRating:
    rating: float
    votes: int | None


class IMDbDatasetIndex:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.ratings: dict[str, DatasetRating] = {}

    def load(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"IMDb ratings dataset not found: {self.path}")
        ratings: dict[str, DatasetRating] = {}
        with gzip.open(self.path, "rt", encoding="utf-8", newline="") as handle:
            header = next(handle, "").rstrip("\r\n").split("\t")
            if header != ["tconst", "averageRating", "numVotes"]:
                raise ValueError("Unexpected IMDb ratings dataset schema")
            for line in handle:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) != 3 or not IMDB_ID.fullmatch(parts[0]):
                    continue
                try:
                    rating, votes = float(parts[1]), int(parts[2])
                except ValueError:
                    continue
                if 0 <= rating <= 10 and votes >= 0:
                    ratings[parts[0]] = DatasetRating(rating, votes)
        self.ratings = ratings

    def get(self, imdb_id: str | None) -> DatasetRating | None:
        return self.ratings.get(imdb_id or "")


class IMDbRatingEnrichmentService:
    operation = OPERATION

    def __init__(self, db: Session, dataset: IMDbDatasetIndex):
        self.db = db
        self.dataset = dataset
        self.tmdb = TMDbMovieService()
        self.fallback = configured_rating_provider()

    def _state(self) -> OperationState:
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if state is None:
            state = OperationState(name=self.operation, details={})
            self.db.add(state)
            self.db.flush()
        return state

    @staticmethod
    def _valid_id_exists():
        return exists().where(
            ExternalId.movie_id == Movie.id,
            func.lower(ExternalId.provider).in_(("imdb", "imdb_id")),
            ExternalId.external_id.op("~")(r"^tt[0-9]{7,10}$"),
        )

    @staticmethod
    def _rating_missing():
        return ~exists().where(
            MovieRating.movie_id == Movie.id,
            func.lower(MovieRating.source) == "imdb",
            MovieRating.rating.is_not(None),
            MovieRating.rating > 0,
            MovieRating.rating <= 10,
        )

    @staticmethod
    def _rating_valid():
        return exists().where(
            MovieRating.movie_id == Movie.id,
            func.lower(MovieRating.source) == "imdb",
            MovieRating.rating > 0,
            MovieRating.rating <= 10,
        )

    @staticmethod
    def _votes_missing():
        return ~exists().where(
            MovieRating.movie_id == Movie.id,
            func.lower(MovieRating.source) == "imdb",
            MovieRating.vote_count.is_not(None),
            MovieRating.vote_count >= 0,
        )

    def coverage(self) -> dict:
        total = self.db.query(func.count(Movie.id)).scalar() or 0
        missing_id = self.db.query(func.count(Movie.id)).filter(~self._valid_id_exists()).scalar() or 0
        missing_rating = self.db.query(func.count(Movie.id)).filter(self._rating_missing()).scalar() or 0
        return {
            "total_movies": total,
            "missing_imdb_id": missing_id,
            "missing_imdb_rating": missing_rating,
            "with_valid_imdb_id": total - missing_id,
            "with_imdb_rating": total - missing_rating,
        }

    def ott_snapshot(self) -> dict:
        count, latest = self.db.query(
            func.count(OttAvailability.id), func.max(OttAvailability.updated_at)
        ).one()
        return {"row_count": int(count or 0), "latest_updated_at": latest.isoformat() if latest else None}

    def _attempted(self):
        return exists().where(
            BackfillRecord.operation == self.operation,
            BackfillRecord.entity_type == "movie",
            BackfillRecord.entity_id == Movie.id,
        )

    @staticmethod
    def _release():
        return func.coalesce(Movie.theatrical_release_date, Movie.release_date)

    @staticmethod
    def language_bucket(language: str | None) -> str:
        return language if language in LANGUAGES else "other"

    def _queue_filter(self, queue: str):
        if queue == "A":
            return self._valid_id_exists() & self._rating_missing()
        if queue == "B":
            return self._valid_id_exists() & self._rating_valid() & self._votes_missing()
        if queue == "C":
            return ~self._valid_id_exists() & self._rating_missing()
        raise ValueError(f"Unknown IMDb queue: {queue}")

    def groups(self, queue: str = "A") -> list[tuple[int | None, str, int]]:
        release = self._release()
        rows = (
            self.db.query(
                extract("year", release).label("year"),
                Movie.original_language,
                func.count(Movie.id),
            )
            .filter(self._queue_filter(queue), ~self._attempted())
            .group_by(extract("year", release), Movie.original_language)
            .all()
        )
        combined: dict[tuple[int | None, str], int] = {}
        for year, language, count in rows:
            key = (int(year) if year is not None else None, self.language_bucket(language))
            combined[key] = combined.get(key, 0) + int(count)
        language_rank = {language: index for index, language in enumerate((*LANGUAGES, "other"), 1)}

        def group_key(item):
            (year, language), _count = item
            if year in RECENT_YEARS:
                year_key = (RECENT_YEARS.index(year), 0)
            else:
                year_key = (len(RECENT_YEARS), -(year or -1))
            return (*year_key, language_rank[language])

        return [(year, language, count) for (year, language), count in sorted(combined.items(), key=group_key)]

    def targets_for_group(self, year: int | None, language: str, limit: int = 500, queue: str = "A") -> list[Movie]:
        release = self._release()
        filters = [self._queue_filter(queue), ~self._attempted()]
        filters.append(extract("year", release) == year if year is not None else release.is_(None))
        if language == "other":
            filters.append(or_(Movie.original_language.is_(None), ~Movie.original_language.in_(LANGUAGES)))
        else:
            filters.append(Movie.original_language == language)
        return (
            self.db.query(Movie)
            .filter(*filters)
            .order_by(
                case((self._valid_id_exists(), 0), else_=1),
                case((Movie.tmdb_id.is_not(None), 0), else_=1),
                case((Movie.title.is_not(None) & (Movie.title != "") & release.is_not(None), 0), else_=1),
                Movie.popularity.desc().nullslast(),
                Movie.id,
            )
            .limit(max(1, min(int(limit), 500)))
            .all()
        )

    def quality_targets(self) -> list[Movie]:
        base = (
            self.db.query(Movie)
            .filter(self._queue_filter("A"), ~self._attempted())
            .order_by(func.md5(cast(Movie.id, String)))
        )
        indian = base.filter(Movie.original_language.in_(LANGUAGES[:5])).limit(10).all()
        international = base.filter(
            or_(Movie.original_language.is_(None), ~Movie.original_language.in_(LANGUAGES[:5]))
        ).limit(10).all()
        return [*indian, *international]

    def _record(self, movie_id: int) -> BackfillRecord:
        record = self.db.query(BackfillRecord).filter_by(
            operation=self.operation, entity_type="movie", entity_id=movie_id
        ).first()
        if record is None:
            record = BackfillRecord(operation=self.operation, entity_type="movie", entity_id=movie_id)
            self.db.add(record)
            self.db.flush()
        return record

    def _imdb_row(self, movie_id: int) -> ExternalId | None:
        return self.db.query(ExternalId).filter(
            ExternalId.movie_id == movie_id,
            func.lower(ExternalId.provider).in_(("imdb", "imdb_id")),
        ).order_by(case((func.lower(ExternalId.provider) == "imdb", 0), else_=1), ExternalId.id).first()

    def _resolve_id(self, movie: Movie) -> tuple[str | None, str, str | None]:
        existing = self._imdb_row(movie.id)
        if existing and IMDB_ID.fullmatch(existing.external_id or ""):
            return existing.external_id, "EXISTING_IMDB_ID", None
        try:
            with metadata_request_context(movie.id, movie.tmdb_id):
                payload = self.tmdb.get_movie_external_ids(movie.tmdb_id)
        except TMDbRequestError as exc:
            return None, "TMDB_SOURCE_FAILURE", sanitize_error(exc)
        imdb_id = str((payload or {}).get("imdb_id") or "").strip()
        if not IMDB_ID.fullmatch(imdb_id):
            return None, "NO_IMDB_ID", None
        duplicate = self.db.query(ExternalId).filter(
            func.lower(ExternalId.provider).in_(("imdb", "imdb_id")),
            ExternalId.external_id == imdb_id,
            ExternalId.movie_id != movie.id,
        ).first()
        if duplicate:
            return None, "AMBIGUOUS_DUPLICATE_ID", f"{imdb_id} already belongs to movie {duplicate.movie_id}"
        if existing:
            existing.provider = "imdb"
            existing.external_id = imdb_id
            existing.source_url = f"https://www.imdb.com/title/{imdb_id}/"
        else:
            self.db.add(
                ExternalId(
                    movie_id=movie.id,
                    provider="imdb",
                    external_id=imdb_id,
                    source_url=f"https://www.imdb.com/title/{imdb_id}/",
                )
            )
        return imdb_id, "TMDB_EXTERNAL_ID", None

    def _fetch_rating(self, imdb_id: str) -> tuple[DatasetRating | None, str, str | None, bool]:
        value = self.dataset.get(imdb_id)
        if value:
            return value, "IMDb official title.ratings", None, False
        if not self.fallback:
            return None, "IMDb official title.ratings", None, False
        last_error = None
        for attempt in range(1, 4):
            try:
                result = self.fallback.fetch(imdb_id)
                if result and result.rating is not None:
                    votes = result.vote_count if result.vote_count is not None and result.vote_count >= 0 else None
                    return DatasetRating(result.rating, votes), "Approved IMDb integration", None, False
                return None, "Approved IMDb integration", None, False
            except ProviderQuotaExhausted as exc:
                return None, "Approved IMDb integration", sanitize_error(exc), True
            except (ProviderRateLimited, ProviderUnavailable) as exc:
                last_error = sanitize_error(exc)
                if attempt < 3:
                    time.sleep(2 ** (attempt - 1))
        return None, "Approved IMDb integration", last_error, True

    def _save_rating(self, movie: Movie, imdb_id: str, value: DatasetRating | None) -> MovieRating:
        now = datetime.now(timezone.utc)
        row = self.db.query(MovieRating).filter(
            MovieRating.movie_id == movie.id,
            func.lower(MovieRating.source) == "imdb",
        ).first()
        if row is None:
            row = MovieRating(movie_id=movie.id, source="IMDb")
            self.db.add(row)
        row.source = "IMDb"
        row.attempt_count = (row.attempt_count or 0) + 1
        row.last_attempt_at = now
        row.last_updated_at = now
        if value is None:
            if row.rating is None or not 0 < row.rating <= 10:
                row.status = RATING_NOT_YET_RATED
            row.next_check_at = now + timedelta(days=30)
            row.last_error = "No rating row in current official IMDb title.ratings dataset"
        else:
            if row.rating is None or not 0 < row.rating <= 10:
                row.rating = value.rating
            if value.votes is not None and (row.vote_count is None or row.vote_count < 0):
                row.vote_count = value.votes
            row.status = RATING_AVAILABLE
            row.next_check_at = now + timedelta(days=90)
            row.last_error = None
        return row

    def _process(self, movie: Movie, batch: int, item: int, total: int) -> dict:
        old_id_row = self._imdb_row(movie.id)
        old_id = old_id_row.external_id if old_id_row and IMDB_ID.fullmatch(old_id_row.external_id or "") else None
        rating_row = self.db.query(MovieRating).filter(
            MovieRating.movie_id == movie.id, func.lower(MovieRating.source) == "imdb"
        ).first()
        before_rating = rating_row.rating if rating_row else None
        before_votes = rating_row.vote_count if rating_row else None
        record = self._record(movie.id)
        record.status = "RUNNING"
        record.attempts = (record.attempts or 0) + 1
        record.last_attempt_at = datetime.now(timezone.utc)
        record.last_error = None
        self.db.commit()
        imdb_id, id_source, identity_error = self._resolve_id(movie)
        status = "NO_RESULT"
        source_failure = False
        ambiguous = False
        rating_source = id_source
        if id_source == "TMDB_SOURCE_FAILURE":
            source_failure = True
            record = self._record(movie.id)
            record.status = "DEFERRED"
            retry_at = datetime.now(timezone.utc) + timedelta(days=1)
            record.last_error = f"reason={identity_error};retry_at={retry_at.isoformat()}"
        elif id_source == "AMBIGUOUS_DUPLICATE_ID":
            ambiguous = True
            status = "NEEDS_REVIEW"
            record = self._record(movie.id)
            record.status = "NEEDS_REVIEW"
            record.last_error = identity_error
        elif imdb_id:
            value, rating_source, rating_error, rating_source_failed = self._fetch_rating(imdb_id)
            rating_row = self._save_rating(movie, imdb_id, value)
            if value:
                status = "CONFIRMED"
                record.status = "DONE"
                record.last_error = None
            else:
                status = "SOURCE_FAILURE" if rating_source_failed else "NO_RATING_AVAILABLE"
                record.status = "DEFERRED"
                retry_at = datetime.now(timezone.utc) + timedelta(days=1 if rating_source_failed else 30)
                reason = rating_error or "NOT_IN_APPROVED_IMDB_SOURCES"
                record.last_error = f"reason={reason};retry_at={retry_at.isoformat()}"
                source_failure = rating_source_failed
        else:
            record = self._record(movie.id)
            record.status = "DEFERRED"
            retry_at = datetime.now(timezone.utc) + timedelta(days=90)
            record.last_error = f"reason=NO_IMDB_ID_FROM_TMDB;retry_at={retry_at.isoformat()}"
        self.db.commit()
        rating_row = self.db.query(MovieRating).filter(
            MovieRating.movie_id == movie.id, func.lower(MovieRating.source) == "imdb"
        ).first()
        release = movie.theatrical_release_date or movie.release_date
        result = {
            "movie_id": movie.id,
            "title": movie.title,
            "year": release.year if release else None,
            "language": movie.original_language,
            "tmdb_id": movie.tmdb_id,
            "old_imdb_id": old_id,
            "imdb_id": imdb_id,
            "rating": rating_row.rating if rating_row else None,
            "votes": rating_row.vote_count if rating_row else None,
            "source": rating_source,
            "status": status,
            "id_added": int(not old_id and bool(imdb_id)),
            "rating_added": int(
                not (before_rating is not None and 0 < before_rating <= 10)
                and bool(rating_row and rating_row.rating is not None and 0 < rating_row.rating <= 10)
            ),
            "votes_added": int(
                not (before_votes is not None and before_votes >= 0)
                and bool(rating_row and rating_row.vote_count is not None and rating_row.vote_count >= 0)
            ),
            "confirmed_existing": int(
                before_rating is not None
                and 0 < before_rating <= 10
                and bool(rating_row and rating_row.rating == before_rating)
            ),
            "source_failure": int(source_failure),
            "ambiguous": int(ambiguous),
            "false_match": 0,
        }
        print(
            f"BATCH: {batch} ITEM: {item}/{total} MOVIE: {movie.title} YEAR: {result['year'] or ''} "
            f"LANGUAGE: {movie.original_language or ''} IMDB_ID: {imdb_id or 'NULL'} "
            f"IMDB_RATING: {result['rating'] if result['rating'] is not None else 'NULL'} "
            f"VOTES: {result['votes'] if result['votes'] is not None else 'NULL'} "
            f"SOURCE: {result['source']} STATUS: {status}",
            flush=True,
        )
        return result

    def run_quality_test(self) -> dict:
        ott_snapshot_before = self.ott_snapshot()
        state = self._state()
        state.status = "QUALITY_TEST"
        state.total_count = self.coverage()["missing_imdb_rating"]
        self.db.commit()
        targets = self.quality_targets()
        items = [self._process(movie, 0, index, len(targets)) for index, movie in enumerate(targets, 1)]
        report = {
            "test_movies": len(items),
            "imdb_ids_found": sum(bool(item["imdb_id"]) for item in items),
            "imdb_ratings_found": sum(item["rating_added"] for item in items),
            "vote_counts_found": sum(item["votes_added"] for item in items),
            "no_result": sum(item["status"] in {"NO_RESULT", "NO_RATING_AVAILABLE"} for item in items),
            "ambiguous": sum(item["ambiguous"] for item in items),
            "false_matches": sum(item["false_match"] for item in items),
            "items": items,
        }
        passed = report["imdb_ratings_found"] >= 15 and report["false_matches"] == 0
        details = dict(state.details or {})
        quality_history = list(details.get("quality_tests") or [])
        quality_history.append(report | {"passed": passed})
        if passed:
            details.pop("halt_reason", None)
        details.update(
            {
                "source": IMDB_DATASET_URL,
                "dataset_path": str(self.dataset.path),
                "quality_test": report,
                "quality_tests": quality_history,
                "quality_gate_passed": passed,
                "coverage_before": self.coverage(),
                "ott_snapshot_before": ott_snapshot_before,
            }
        )
        state.details = details
        state.processed_count = (
            self.db.query(func.count(BackfillRecord.id))
            .filter(BackfillRecord.operation == self.operation)
            .scalar()
            or 0
        )
        state.status = "IDLE" if passed else "BLOCKED"
        state.last_success_at = datetime.now(timezone.utc)
        state.last_error = None if passed else "Quality gate failed"
        self.db.commit()
        return report | {"passed": passed}

    def run_group_batch(self, queue: str, year: int | None, language: str, batch_size: int = 500) -> dict:
        state = self._state()
        details = dict(state.details or {})
        batch = int(details.get("batches_completed") or 0) + 1
        targets = self.targets_for_group(year, language, batch_size, queue)
        state.status = "RUNNING"
        state.last_error = None
        state.details = details | {"current_queue": queue, "current_year": year, "current_language": language}
        self.db.commit()
        report = {
            "batch": batch, "queue": queue, "year": year, "language": language, "processed": 0,
            "imdb_ids_added": 0, "imdb_ratings_added": 0, "vote_counts_added": 0,
            "confirmed_existing": 0, "no_result": 0, "needs_review": 0,
            "ambiguous": 0, "source_failures": 0, "errors": 0,
        }
        for item_number, movie in enumerate(targets, 1):
            try:
                item = self._process(movie, batch, item_number, len(targets))
                report["imdb_ids_added"] += item["id_added"]
                report["imdb_ratings_added"] += item["rating_added"]
                report["vote_counts_added"] += item["votes_added"]
                report["confirmed_existing"] += item["confirmed_existing"]
                report["source_failures"] += item["source_failure"]
                report["ambiguous"] += item["ambiguous"]
                report["needs_review"] += int(item["status"] == "NEEDS_REVIEW")
                report["no_result"] += int(item["status"] in {"NO_RESULT", "NO_RATING_AVAILABLE"})
            except Exception as exc:
                self.db.rollback()
                record = self._record(movie.id)
                record.status = "FAILED"
                record.attempts = max(record.attempts or 1, 1)
                record.last_attempt_at = datetime.now(timezone.utc)
                retry_at = datetime.now(timezone.utc) + timedelta(days=1)
                record.last_error = f"reason={sanitize_error(exc)};retry_at={retry_at.isoformat()}"
                self.db.commit()
                report["errors"] += 1
            report["processed"] += 1
            state = self._state()
            state.cursor = movie.id
            state.processed_count = (state.processed_count or 0) + 1
            self.db.commit()
        processed = report["processed"]
        for numerator, rate in (
            ("imdb_ids_added", "imdb_id_hit_rate"),
            ("imdb_ratings_added", "imdb_rating_hit_rate"),
            ("vote_counts_added", "vote_count_hit_rate"),
        ):
            report[rate] = round(100 * report[numerator] / processed, 2) if processed else 0.0
        report["remaining_in_group"] = len(self.targets_for_group(year, language, 1, queue))
        state = self._state()
        details = dict(state.details or {})
        cumulative = dict(details.get("cumulative") or {})
        for key in (
            "processed", "imdb_ids_added", "imdb_ratings_added", "vote_counts_added",
            "confirmed_existing", "no_result", "needs_review", "ambiguous", "source_failures", "errors",
        ):
            cumulative[key] = int(cumulative.get(key) or 0) + int(report[key])
        history = list(details.get("batch_history") or [])
        history.append(report)
        details.update(
            {
                "batches_completed": batch,
                "batch_size": batch_size,
                "last_batch": report,
                "batch_history": history,
                "cumulative": cumulative,
            }
        )
        state.details = details
        state.status = "IDLE"
        state.last_success_at = datetime.now(timezone.utc)
        self.db.commit()
        return report | {"cumulative": cumulative}
