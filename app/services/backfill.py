"""Accelerated, resumable initial-data and targeted-repair pipelines.

Normal beat jobs remain deliberately conservative. These services are invoked by
administrator actions or the sequential repair orchestrator and checkpoint every
entity so a worker restart never loses successful work.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, exists, func, or_
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieCredit, MovieImage, MovieRating, MovieTrailer, Person
from app.models.operations import BackfillRecord, MovieRequest, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.image_fallback import ImageFallbackService
from app.services.movie_metadata_service import MovieMetadataService
from app.services.operations import DataHealthService, OttResearchService
from app.services.release_status import ReleaseStatusService
from app.services.rating_provider import (
    IMDB_ID,
    IMDbRatingRefreshService,
    MovieRatingProvider,
    ProviderQuotaExhausted,
    ProviderRateLimited,
    RATING_BLOCKED_BY_QUOTA,
    RATING_TEMPORARY_FAILURE,
    apply_rating_result,
    configured_rating_provider,
    ensure_pending_rating,
    mark_rating_failure,
)
from app.services.tmdb.client import TMDbRequestError
from app.services.tmdb.movie_service import TMDbMovieService
from app.services.trailers import TrailerService


METADATA = "tmdb.metadata_backfill"
PEOPLE = "tmdb.person_backfill"
IMAGES = "operations.image_backfill"
TRAILERS = "tmdb.trailer_backfill"
IMDB = "ratings.imdb_backfill"
IMDB_IDS = "ratings.imdb_id_backfill"
OTT = "operations.ott_eligibility_backfill"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResumableBackfill:
    operation = "backfill"

    def __init__(self, db: Session):
        self.db = db

    def state(self, total: int | None = None) -> OperationState:
        item = self.db.query(OperationState).filter_by(name=self.operation).first()
        if not item:
            item = OperationState(name=self.operation)
            self.db.add(item)
            self.db.flush()
        if total is not None:
            item.total_count = max(item.total_count, total)
        return item

    def _eligible(self, entity_type: str, entity_id_column):
        done = exists().where(
            (BackfillRecord.operation == self.operation)
            & (BackfillRecord.entity_type == entity_type)
            & (BackfillRecord.entity_id == entity_id_column)
            & (BackfillRecord.status == "DONE")
        )
        exhausted = exists().where(
            (BackfillRecord.operation == self.operation)
            & (BackfillRecord.entity_type == entity_type)
            & (BackfillRecord.entity_id == entity_id_column)
            & (BackfillRecord.attempts >= settings.BACKFILL_MAX_ATTEMPTS)
        )
        return ~done & ~exhausted

    def _record(self, entity_type: str, entity_id: int) -> BackfillRecord:
        record = self.db.query(BackfillRecord).filter_by(
            operation=self.operation, entity_type=entity_type, entity_id=entity_id
        ).first()
        if not record:
            record = BackfillRecord(operation=self.operation, entity_type=entity_type, entity_id=entity_id)
            self.db.add(record)
            self.db.flush()
        return record

    def _start(self, entity_type: str, entity_id: int) -> BackfillRecord:
        record = self._record(entity_type, entity_id)
        record.status = "RUNNING"
        record.attempts += 1
        record.last_attempt_at = _now()
        record.last_error = None
        self.db.commit()
        return record

    def _finish(self, entity_type: str, entity_id: int, error: Exception | None = None) -> None:
        record = self._record(entity_type, entity_id)
        record.status = "FAILED" if error else "DONE"
        record.last_error = sanitize_error(error) if error else None
        self.db.commit()

    def _summary(self, state: OperationState, processed: int, succeeded: int, failed: int, complete: bool) -> dict:
        state.status = "COMPLETE" if complete else "PAUSED"
        completed_records = self.db.query(BackfillRecord).filter(
            BackfillRecord.operation == self.operation,
            or_(
                BackfillRecord.status == "DONE",
                (BackfillRecord.status == "FAILED") & (BackfillRecord.attempts >= settings.BACKFILL_MAX_ATTEMPTS),
            ),
        ).count()
        state.processed_count = completed_records
        state.last_success_at = _now()
        state.last_error = None if not failed else f"{failed} record(s) failed in the latest batch"
        if complete:
            state.completed_at = _now()
        self.db.commit()
        return {
            "operation": self.operation,
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
            "processed_total": state.processed_count,
            "total": state.total_count,
            "remaining": max(0, state.total_count - state.processed_count),
            "cursor": state.cursor,
            "complete": complete,
        }


class MetadataBackfillService(ResumableBackfill):
    operation = METADATA

    def run(self, batch_size: int | None = None) -> dict:
        batch_size = max(1, min(batch_size or settings.METADATA_BACKFILL_BATCH_SIZE, 250))
        state = self.state(self.db.query(Movie).count())
        if state.status == "COMPLETE":
            return self._summary(state, 0, 0, 0, True)
        cast_missing = ~exists().where((MovieCredit.movie_id == Movie.id) & (MovieCredit.credit_type == "cast"))
        crew_missing = ~exists().where((MovieCredit.movie_id == Movie.id) & (MovieCredit.credit_type == "crew"))
        imdb_missing = ~exists().where((ExternalId.movie_id == Movie.id) & (func.lower(ExternalId.provider) == "imdb"))
        images_missing = ~exists().where(MovieImage.movie_id == Movie.id)
        movies = self.db.query(Movie).filter(self._eligible("movie", Movie.id)).order_by(
            case(
                (cast_missing, 0),
                (crew_missing, 1),
                (Movie.poster_path.is_(None), 2),
                (Movie.backdrop_path.is_(None), 3),
                (images_missing, 4),
                (imdb_missing, 5),
                else_=6,
            ),
            Movie.id,
        ).limit(batch_size).all()
        if not movies:
            return self._summary(state, 0, 0, 0, True)
        state.status = "RUNNING"; state.completed_at = None; self.db.commit()
        service = MovieMetadataService(self.db)
        succeeded = failed = 0
        for movie in movies:
            movie_id = movie.id
            self._start("movie", movie_id)
            try:
                service.enrich_movie(self.db.get(Movie, movie_id))
                self._finish("movie", movie_id); succeeded += 1
            except Exception as exc:  # one provider or record failure must not abort the batch
                self.db.rollback(); self._finish("movie", movie_id, exc); failed += 1
            state = self.state(); state.cursor = movie_id; self.db.commit()
        remaining = self.db.query(Movie.id).filter(self._eligible("movie", Movie.id)).first() is not None
        return self._summary(state, len(movies), succeeded, failed, not remaining)


class PersonBackfillService(ResumableBackfill):
    operation = PEOPLE

    def run(self, batch_size: int | None = None) -> dict:
        batch_size = max(1, min(batch_size or settings.PERSON_BACKFILL_BATCH_SIZE, 250))
        total = self.db.query(Person).filter(Person.tmdb_id.is_not(None)).count()
        state = self.state(total)
        if state.status == "COMPLETE":
            return self._summary(state, 0, 0, 0, True)
        people = self.db.query(Person).filter(Person.tmdb_id.is_not(None), self._eligible("person", Person.id)).order_by(
            case((Person.profile_path.is_(None), 0), (Person.biography.is_(None), 1), (Person.imdb_id.is_(None), 2), else_=3),
            Person.id,
        ).limit(batch_size).all()
        if not people:
            return self._summary(state, 0, 0, 0, True)
        state.status = "RUNNING"; state.completed_at = None; self.db.commit()
        tmdb = TMDbMovieService(); succeeded = failed = 0
        for person in people:
            person_id = person.id; tmdb_id = person.tmdb_id
            self._start("person", person_id)
            try:
                payload = tmdb.get_person_details(tmdb_id)
                person = self.db.get(Person, person_id)
                for field in ("name", "profile_path", "biography", "place_of_birth", "known_for_department"):
                    value = payload.get(field)
                    if value not in (None, ""):
                        setattr(person, field, value)
                if payload.get("birthday"):
                    try: person.birthday = date.fromisoformat(payload["birthday"][:10])
                    except (TypeError, ValueError): pass
                external = payload.get("external_ids") or {}
                if external.get("imdb_id"):
                    person.imdb_id = external["imdb_id"]
                self.db.commit(); self._finish("person", person_id); succeeded += 1
            except Exception as exc:
                self.db.rollback(); self._finish("person", person_id, exc); failed += 1
            state = self.state(); state.cursor = person_id; self.db.commit()
        remaining = self.db.query(Person.id).filter(Person.tmdb_id.is_not(None), self._eligible("person", Person.id)).first() is not None
        return self._summary(state, len(people), succeeded, failed, not remaining)


class ImageBackfillService(ResumableBackfill):
    operation = IMAGES

    def run(self, batch_size: int | None = None) -> dict:
        batch_size = max(1, min(batch_size or settings.IMAGE_BACKFILL_BATCH_SIZE, 250))
        total = self.db.query(Movie).count() + self.db.query(Person).count()
        state = self.state(total)
        if state.status == "COMPLETE":
            return self._summary(state, 0, 0, 0, True)
        movies = self.db.query(Movie).filter(self._eligible("movie", Movie.id)).order_by(Movie.id).limit(batch_size).all()
        people = [] if len(movies) == batch_size else self.db.query(Person).filter(
            self._eligible("person", Person.id)
        ).order_by(Person.id).limit(batch_size - len(movies)).all()
        if not movies and not people:
            return self._summary(state, 0, 0, 0, True)
        state.status = "RUNNING"; state.completed_at = None; self.db.commit()
        images = ImageFallbackService(self.db); succeeded = failed = 0
        for movie in movies:
            entity_id = movie.id; self._start("movie", entity_id)
            try:
                current = self.db.get(Movie, entity_id)
                images.recover_movie(current, "poster")
                images.recover_movie(current, "backdrop")
                if self.db.query(MovieImage.id).filter_by(movie_id=entity_id, image_type="logo").first():
                    images.recover_movie(current, "logo")
                self._finish("movie", entity_id); succeeded += 1
            except Exception as exc:
                self.db.rollback(); self._finish("movie", entity_id, exc); failed += 1
            state = self.state(); state.cursor = entity_id; self.db.commit()
        for person in people:
            entity_id = person.id; self._start("person", entity_id)
            try:
                images.recover_person(self.db.get(Person, entity_id))
                self._finish("person", entity_id); succeeded += 1
            except Exception as exc:
                self.db.rollback(); self._finish("person", entity_id, exc); failed += 1
            state = self.state(); state.cursor = entity_id; self.db.commit()
        remaining = (
            self.db.query(Movie.id).filter(self._eligible("movie", Movie.id)).first() is not None
            or self.db.query(Person.id).filter(self._eligible("person", Person.id)).first() is not None
        )
        return self._summary(state, len(movies) + len(people), succeeded, failed, not remaining)


class TrailerBackfillService(ResumableBackfill):
    """Checkpointed trailer-only backfill that never restarts metadata work."""

    operation = TRAILERS

    def run(self, batch_size: int | None = None) -> dict:
        batch_size = max(1, min(batch_size or settings.TRAILER_BACKFILL_BATCH_SIZE, 100))
        total = self.db.query(Movie).count()
        state = self.state(total)
        if not (settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN):
            state.status = "BLOCKED"
            state.last_error = "External metadata integration is not configured"
            self.db.commit()
            return {"operation": self.operation, "configured": False, "processed": 0, "complete": False}
        has_trailer = exists().where(
            (MovieTrailer.movie_id == Movie.id) & (MovieTrailer.is_primary.is_(True))
        )
        requested = exists().where(
            (MovieRequest.external_movie_id == Movie.tmdb_id)
            & (MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"]))
        )
        today = date.today()
        movies = self.db.query(Movie).filter(
            ~has_trailer,
            self._eligible("movie", Movie.id),
        ).order_by(
            case(
                (requested, 0),
                (Movie.release_date >= today, 1),
                (Movie.release_date >= today - timedelta(days=730), 2),
                else_=3,
            ),
            Movie.popularity.desc().nullslast(),
            Movie.id,
        ).limit(batch_size).all()
        if not movies:
            return self._summary(state, 0, 0, 0, True)
        state.status = "RUNNING"
        state.completed_at = None
        self.db.commit()
        provider = TMDbMovieService()
        service = TrailerService(self.db)
        succeeded = failed = 0
        stopped = None
        for movie in movies:
            movie_id = movie.id
            self._start("movie", movie_id)
            try:
                payload = provider.get_movie_videos(movie.tmdb_id)
                service.upsert(self.db.get(Movie, movie_id), payload, commit=True)
                self._finish("movie", movie_id)
                succeeded += 1
            except TMDbRequestError as exc:
                self.db.rollback()
                self._finish("movie", movie_id, exc)
                failed += 1
                if exc.status_code == 429:
                    stopped = "provider_rate_limited"
                    break
            except Exception as exc:
                self.db.rollback()
                self._finish("movie", movie_id, exc)
                failed += 1
            state = self.state()
            state.cursor = movie_id
            self.db.commit()
        remaining = self.db.query(Movie.id).filter(
            ~has_trailer,
            self._eligible("movie", Movie.id),
        ).first() is not None
        result = self._summary(state, succeeded + failed, succeeded, failed, not remaining)
        if stopped:
            state = self.state()
            state.status = "PAUSED"
            state.last_error = stopped
            self.db.commit()
            result.update({"complete": False, "stopped": stopped})
        return result


class IMDbIdRecoveryService(ResumableBackfill):
    """Recover missing IMDb IDs without reopening completed metadata work."""

    operation = IMDB_IDS

    def _due(self, record: BackfillRecord | None, now: datetime) -> bool:
        if record is None:
            return True
        if record.status in {"DONE", "PERMANENT"}:
            return False
        attempted = record.last_attempt_at
        if attempted and attempted.tzinfo is None:
            attempted = attempted.replace(tzinfo=timezone.utc)
        if not attempted:
            return True
        if record.status == "PENDING":
            return attempted <= now - timedelta(days=90)
        delay_days = min(30, max(1, 2 ** min(record.attempts or 0, 5)))
        return attempted <= now - timedelta(days=delay_days)

    def run(self, batch_size: int | None = None) -> dict:
        batch_size = max(1, min(batch_size or settings.IMDB_ID_BACKFILL_BATCH_SIZE, 100))
        total = self.db.query(Movie).count()
        known_ids = self.db.query(func.count(func.distinct(ExternalId.movie_id))).filter(
            func.lower(ExternalId.provider) == "imdb"
        ).scalar() or 0
        state = self.state(total)
        state.processed_count = known_ids
        if not (settings.TMDB_API_KEY or settings.TMDB_ACCESS_TOKEN):
            state.status = "BLOCKED"
            state.last_error = "External metadata integration is not configured"
            self.db.commit()
            return {"operation": self.operation, "configured": False, "processed": 0, "recovered": 0, "complete": False}

        missing_id = ~exists().where(
            (ExternalId.movie_id == Movie.id) & (func.lower(ExternalId.provider) == "imdb")
        )
        requested = exists().where(
            (MovieRequest.external_movie_id == Movie.tmdb_id)
            & (MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"]))
        )
        today = date.today()
        candidates = self.db.query(Movie).filter(missing_id).order_by(
            case(
                ((Movie.release_date <= today) & (Movie.popularity >= 50), 0),
                ((Movie.release_date <= today) & (Movie.release_date >= today - timedelta(days=730)), 1),
                (requested, 2),
                (Movie.release_date <= today, 3),
                else_=4,
            ),
            Movie.popularity.desc().nullslast(),
            Movie.release_date.desc().nullslast(),
            Movie.id,
        ).limit(batch_size * 10).all()
        now = _now()
        rows = []
        for movie in candidates:
            record = self.db.query(BackfillRecord).filter_by(
                operation=self.operation, entity_type="movie", entity_id=movie.id
            ).first()
            if self._due(record, now):
                rows.append(movie)
            if len(rows) >= batch_size:
                break

        if not rows:
            state.status = "COMPLETE" if known_ids == total else "PAUSED"
            state.last_success_at = now
            state.last_error = None
            self.db.commit()
            return {
                "operation": self.operation,
                "configured": True,
                "processed": 0,
                "recovered": 0,
                "complete": True,
                "catalog_complete": known_ids == total,
            }

        service = TMDbMovieService()
        state.status = "RUNNING"
        state.completed_at = None
        self.db.commit()
        processed = recovered = permanent = failed = 0
        stopped = None
        for candidate in rows:
            movie_id = candidate.id
            tmdb_id = candidate.tmdb_id
            self._start("movie", movie_id)
            try:
                payload = service.get_movie_external_ids(tmdb_id)
                imdb_id = (payload.get("imdb_id") or "").strip()
                record = self._record("movie", movie_id)
                if IMDB_ID.fullmatch(imdb_id):
                    duplicate = self.db.query(ExternalId.id).filter_by(
                        provider="imdb", external_id=imdb_id
                    ).first()
                    if not duplicate:
                        self.db.add(ExternalId(movie_id=movie_id, provider="imdb", external_id=imdb_id))
                        ensure_pending_rating(self.db, movie_id)
                        record.status = "DONE"
                        record.last_error = None
                    else:
                        record.status = "PERMANENT"
                        record.last_error = "IMDb ID is already attached to another movie"
                        permanent += 1
                    recovered += int(not duplicate)
                else:
                    record.status = "PENDING"
                    record.last_error = "No IMDb ID is currently available"
                self.db.commit()
                processed += 1
            except TMDbRequestError as exc:
                self.db.rollback()
                record = self._record("movie", movie_id)
                if exc.status_code == 404 or (exc.permanent and exc.status_code not in {401, 403}):
                    record.status = "PERMANENT"
                    record.last_error = f"External metadata record unavailable (HTTP {exc.status_code})"
                    permanent += 1
                    processed += 1
                    self.db.commit()
                else:
                    record.status = "PENDING" if exc.status_code in {401, 403, 429} else "FAILED"
                    record.last_error = sanitize_error(exc)
                    state = self.state()
                    state.status = "BLOCKED" if exc.status_code in {401, 403, 429} else "PAUSED"
                    state.last_failure_at = now
                    state.last_error = sanitize_error(exc)
                    self.db.commit()
                    stopped = "provider_blocked" if state.status == "BLOCKED" else "provider_unavailable"
                    break
            except Exception as exc:
                self.db.rollback()
                record = self._record("movie", movie_id)
                record.status = "FAILED"
                record.last_error = sanitize_error(exc)
                state = self.state()
                state.status = "PAUSED"
                state.last_failure_at = now
                state.last_error = sanitize_error(exc)
                self.db.commit()
                failed += 1
                stopped = "provider_unavailable"
                break
            state = self.state()
            state.cursor = movie_id
            self.db.commit()

        known_ids = self.db.query(func.count(func.distinct(ExternalId.movie_id))).filter(
            func.lower(ExternalId.provider) == "imdb"
        ).scalar() or 0
        state = self.state(total)
        state.processed_count = known_ids
        if not stopped:
            state.status = "PAUSED"
            state.last_success_at = _now()
            state.last_error = None
        self.db.commit()
        return {
            "operation": self.operation,
            "configured": True,
            "processed": processed,
            "recovered": recovered,
            "permanent": permanent,
            "failed": failed,
            "complete": not stopped and len(rows) < batch_size,
            "catalog_complete": known_ids == total,
            "stopped": stopped,
        }


class IMDbBackfillService(ResumableBackfill):
    """Drain the current due IMDb rating queue using lifecycle checkpoints."""

    operation = IMDB

    def __init__(self, db: Session, provider: MovieRatingProvider | None = None):
        super().__init__(db)
        self.provider = provider if provider is not None else configured_rating_provider()

    def run(self, batch_size: int | None = None) -> dict:
        service = IMDbRatingRefreshService(self.db, self.provider)
        service.operation = self.operation
        result = service.refresh(max(1, min(batch_size or settings.IMDB_BACKFILL_BATCH_SIZE, 100)))
        return {
            "operation": self.operation,
            "succeeded": result.get("processed", 0) if not result.get("stopped") else 0,
            "failed": 1 if result.get("stopped") == "provider_unavailable" else 0,
        } | result


class OttQueueBackfillService(ResumableBackfill):
    operation = OTT

    def _missing(self):
        return self.db.query(Movie).outerjoin(OttAvailability).filter(
            Movie.ott_research_eligibility == "ELIGIBLE"
        ).group_by(Movie.id).having(
            (func.count(OttAvailability.id) == 0)
            | (func.count(OttAvailability.ott_release_date) < func.count(OttAvailability.id))
        )

    def run(self, batch_size: int | None = None) -> dict:
        batch_size = max(1, min(batch_size or settings.OTT_BACKFILL_BATCH_SIZE, 500))
        classification = ReleaseStatusService(self.db).classify_batch(batch_size)
        state = self.state(self._missing().count())
        movies = self._missing().filter(self._eligible("movie", Movie.id)).order_by(Movie.id).limit(batch_size).all()
        if not movies:
            result = self._summary(state, 0, 0, 0, classification.get("complete", False))
            result["classification"] = classification
            return result
        state.status = "RUNNING"; state.completed_at = None; self.db.commit()
        service = OttResearchService(self.db, settings.OTT_CONFIRMATION_THRESHOLD); succeeded = failed = queued = 0
        for movie in movies:
            movie_id = movie.id; self._start("movie", movie_id)
            try:
                queued += int(service.queue_movie(movie_id)); self.db.commit()
                self._finish("movie", movie_id); succeeded += 1
            except Exception as exc:
                self.db.rollback(); self._finish("movie", movie_id, exc); failed += 1
            state = self.state(); state.cursor = movie_id; self.db.commit()
        remaining = self._missing().filter(self._eligible("movie", Movie.id)).first() is not None
        result = self._summary(state, len(movies), succeeded, failed, not remaining); result["queued"] = queued; return result


class SingleMovieRepairService:
    """Run the complete bounded repair workflow for one administrator-selected movie."""

    def __init__(self, db: Session, rating_provider: MovieRatingProvider | None = None):
        self.db = db
        self.rating_provider = rating_provider if rating_provider is not None else configured_rating_provider()

    def repair(self, movie_id: int) -> dict:
        movie = self.db.get(Movie, movie_id)
        if not movie:
            raise LookupError("Movie not found")
        result: dict[str, object] = {"movie_id": movie_id, "metadata": "pending", "people": 0, "images": [], "imdb": "not-configured", "ott_queued": False}
        MovieMetadataService(self.db).enrich_movie(movie)
        result["metadata"] = "updated"
        people = self.db.query(Person).join(MovieCredit).filter(MovieCredit.movie_id == movie_id, Person.tmdb_id.is_not(None)).distinct().limit(25).all()
        tmdb = TMDbMovieService()
        for person in people:
            if person.profile_path and person.biography and person.imdb_id:
                continue
            try:
                payload = tmdb.get_person_details(person.tmdb_id)
                person.profile_path = payload.get("profile_path") or person.profile_path
                person.biography = payload.get("biography") or person.biography
                person.place_of_birth = payload.get("place_of_birth") or person.place_of_birth
                person.imdb_id = (payload.get("external_ids") or {}).get("imdb_id") or person.imdb_id
                self.db.commit(); result["people"] = int(result["people"]) + 1
            except Exception:
                self.db.rollback()
        images = ImageFallbackService(self.db); movie = self.db.get(Movie, movie_id)
        for image_type in ("poster", "backdrop"):
            result["images"].append(images.recover_movie(movie, image_type))
        external_id = self.db.query(ExternalId).filter(ExternalId.movie_id == movie_id, func.lower(ExternalId.provider) == "imdb").first()
        rating = self.db.query(MovieRating).filter(MovieRating.movie_id == movie_id, func.lower(MovieRating.source) == "imdb").first()
        if rating and rating.rating is not None:
            result["imdb"] = "already-present"
        elif self.rating_provider and external_id:
            rating = rating or ensure_pending_rating(self.db, movie_id)
            try:
                provider_result = self.rating_provider.fetch(external_id.external_id)
                if provider_result:
                    apply_rating_result(rating, movie, provider_result)
                    self.db.commit(); result["imdb"] = "updated" if provider_result.rating is not None else "not-yet-rated"
            except (ProviderRateLimited, ProviderQuotaExhausted) as exc:
                mark_rating_failure(rating, movie, RATING_BLOCKED_BY_QUOTA, exc, _now())
                self.db.commit(); result["imdb"] = "quota-blocked"
            except Exception as exc:
                mark_rating_failure(rating, movie, RATING_TEMPORARY_FAILURE, exc, _now())
                self.db.commit(); result["imdb"] = "temporary-failure"
        elif not external_id:
            result["imdb"] = "missing-external-id"
        ott = OttResearchService(self.db, settings.OTT_CONFIRMATION_THRESHOLD)
        result["ott_queued"] = ott.queue_movie(movie_id); self.db.commit()
        # Targeted health evaluation is performed without disturbing the global cursor.
        health = DataHealthService(self.db)
        health._set(movie_id, {
            "missing_poster": not movie.poster_path,
            "missing_backdrop": not movie.backdrop_path,
            "missing_cast": not self.db.query(MovieCredit.id).filter_by(movie_id=movie_id, credit_type="cast").first(),
            "missing_director": not self.db.query(MovieCredit.id).filter_by(movie_id=movie_id, credit_type="crew", job="Director").first(),
            "missing_imdb": external_id is None,
            "missing_ott_provider": not self.db.query(OttAvailability.id).filter_by(movie_id=movie_id).first(),
        })
        self.db.commit(); result["data_health"] = "checked"
        return result
