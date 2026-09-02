"""Configurable, lawful IMDb-compatible rating retrieval and refresh."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re

import httpx
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieRating
from app.models.operations import MovieRequest, OperationState


IMDB_ID = re.compile(r"tt\d{7,10}")
RATING_AVAILABLE = "AVAILABLE"
RATING_PENDING = "PENDING"
RATING_NOT_YET_RATED = "NOT_YET_RATED"
RATING_TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
RATING_NOT_FOUND = "NOT_FOUND"
RATING_BLOCKED_BY_QUOTA = "BLOCKED_BY_QUOTA"
RATING_INVALID_ID = "INVALID_ID"
RETRYABLE_RATING_STATES = {
    RATING_PENDING,
    RATING_NOT_YET_RATED,
    RATING_TEMPORARY_FAILURE,
    RATING_NOT_FOUND,
    RATING_BLOCKED_BY_QUOTA,
}


class ProviderRateLimited(RuntimeError):
    """Provider asked the caller to stop the current batch and resume later."""


class ProviderQuotaExhausted(RuntimeError):
    """Provider account has no remaining quota for the current period."""


class ProviderUnavailable(RuntimeError):
    """Provider is temporarily unavailable; the batch must pause cleanly."""


@dataclass(frozen=True)
class RatingResult:
    rating: float | None
    vote_count: int | None
    source_id: str
    checked_at: datetime
    status: str = RATING_AVAILABLE


class MovieRatingProvider(ABC):
    """Interface for approved data providers; implementations must not scrape IMDb."""

    source = "IMDb"

    @abstractmethod
    def fetch(self, imdb_id: str) -> RatingResult | None:
        raise NotImplementedError


class OmdbRatingProvider(MovieRatingProvider):
    """Fetch IMDb-compatible rating fields through a configured OMDb API account."""

    def __init__(self, api_url: str, api_key: str, timeout: float = 15):
        self.api_url = api_url
        self.api_key = api_key
        self.timeout = timeout

    def fetch(self, imdb_id: str) -> RatingResult | None:
        if not IMDB_ID.fullmatch(imdb_id):
            return None
        try:
            response = httpx.get(
                self.api_url,
                params={"apikey": self.api_key, "i": imdb_id},
                timeout=self.timeout,
                follow_redirects=True,
            )
        except httpx.RequestError:
            raise ProviderUnavailable("IMDb rating provider is temporarily unavailable") from None
        status_code = getattr(response, "status_code", 200)
        if status_code == 429:
            raise ProviderRateLimited("IMDb rating provider rate limit reached")
        if status_code in {401, 402, 403}:
            raise ProviderQuotaExhausted("IMDb rating provider account or free quota is unavailable")
        if status_code >= 500:
            raise ProviderUnavailable("IMDb rating provider is temporarily unavailable")
        if status_code >= 400:
            raise ProviderUnavailable(f"IMDb rating provider returned HTTP {status_code}")

        payload = response.json()
        error = str(payload.get("Error", ""))
        if str(payload.get("Response", "True")).lower() == "false" and any(
            phrase in error.lower() for phrase in ("limit", "quota", "too many")
        ):
            raise ProviderQuotaExhausted("IMDb rating provider free quota is exhausted")
        checked_at = datetime.now(timezone.utc)
        if str(payload.get("Response", "True")).lower() == "false":
            return RatingResult(None, None, imdb_id, checked_at, RATING_NOT_FOUND)

        raw_rating = payload.get("imdbRating")
        raw_votes = payload.get("imdbVotes")
        try:
            rating = float(raw_rating) if raw_rating not in (None, "", "N/A") else None
        except (TypeError, ValueError):
            rating = None
        try:
            vote_count = int(str(raw_votes).replace(",", "")) if raw_votes not in (None, "", "N/A") else None
        except (TypeError, ValueError):
            vote_count = None
        if rating is not None and not 0 <= rating <= 10:
            rating = None
        result_status = RATING_AVAILABLE if rating is not None else RATING_NOT_YET_RATED
        return RatingResult(rating, vote_count, imdb_id, checked_at, result_status)


def configured_rating_provider() -> MovieRatingProvider | None:
    provider = settings.IMDB_RATING_PROVIDER.strip().lower()
    if provider == "omdb" and settings.IMDB_RATING_API_URL and settings.IMDB_RATING_API_KEY:
        return OmdbRatingProvider(settings.IMDB_RATING_API_URL, settings.IMDB_RATING_API_KEY)
    return None


def ensure_pending_rating(db: Session, movie_id: int) -> MovieRating:
    """Create lifecycle state when an IMDb ID first becomes available."""

    record = db.query(MovieRating).filter(
        MovieRating.movie_id == movie_id,
        func.lower(MovieRating.source) == "imdb",
    ).first()
    if record is None:
        record = MovieRating(movie_id=movie_id, source="IMDb", status=RATING_PENDING)
        db.add(record)
    return record


def next_rating_check(movie: Movie, status: str, attempts: int, now: datetime) -> datetime | None:
    """Use release-aware backoff so unrated older titles consume very few calls."""

    release_date = movie.theatrical_release_date or movie.release_date
    today = now.date()
    if status == RATING_AVAILABLE:
        days = 30 if (movie.popularity or 0) >= 50 or (release_date and release_date >= today - timedelta(days=365)) else 90
        return now + timedelta(days=days)
    if status == RATING_NOT_YET_RATED:
        if release_date and release_date > today:
            return now + timedelta(days=30)
        age = (today - release_date).days if release_date else None
        return now + timedelta(days=7 if age is not None and age <= 90 else 30 if age is not None and age <= 365 else 180)
    if status == RATING_NOT_FOUND:
        age = (today - release_date).days if release_date and release_date <= today else None
        return now + timedelta(days=30 if age is not None and age <= 365 else 365)
    if status == RATING_BLOCKED_BY_QUOTA:
        return now + timedelta(days=1)
    if status == RATING_TEMPORARY_FAILURE:
        return now + timedelta(hours=min(24 * 7, max(1, 2 ** min(attempts, 8))))
    if status == RATING_PENDING:
        return now
    return None


def apply_rating_result(record: MovieRating, movie: Movie, result: RatingResult) -> None:
    record.source = "IMDb"
    record.rating = result.rating
    record.vote_count = result.vote_count
    record.status = result.status
    record.last_attempt_at = result.checked_at
    record.last_updated_at = result.checked_at
    record.last_error = None
    record.attempt_count = (record.attempt_count or 0) + 1
    record.next_check_at = next_rating_check(movie, result.status, record.attempt_count, result.checked_at)


def mark_rating_failure(record: MovieRating, movie: Movie, status: str, error: object, now: datetime) -> None:
    record.source = "IMDb"
    record.status = status
    record.last_attempt_at = now
    record.last_error = sanitize_error(error, limit=1000)
    record.attempt_count = (record.attempt_count or 0) + 1
    record.next_check_at = next_rating_check(movie, status, record.attempt_count, now)


class IMDbRatingRefreshService:
    """Refresh a bounded due batch with persistent states and quota-safe stopping."""

    operation = "imdb_rating_refresh"

    def __init__(self, db: Session, provider: MovieRatingProvider | None = None):
        self.db = db
        self.provider = provider if provider is not None else configured_rating_provider()

    @staticmethod
    def configuration_status() -> dict:
        missing = []
        if settings.IMDB_RATING_PROVIDER.strip().lower() != "omdb":
            missing.append("IMDB_RATING_PROVIDER")
        if not settings.IMDB_RATING_API_URL:
            missing.append("IMDB_RATING_API_URL")
        if not settings.IMDB_RATING_API_KEY:
            missing.append("IMDB_RATING_API_KEY")
        return {
            "provider": "OMDb",
            "configured": not missing,
            "missing": missing,
        }

    def refresh_movie(self, movie_id: int) -> dict:
        """Refresh exactly one movie, preserving lifecycle/backoff state."""
        movie = self.db.get(Movie, movie_id)
        if not movie:
            raise LookupError("Movie not found")
        external_id = self.db.query(ExternalId).filter(
            ExternalId.movie_id == movie_id,
            func.lower(ExternalId.provider) == "imdb",
        ).first()
        status = self.configuration_status()
        if not external_id:
            return status | {"movie_id": movie_id, "updated": False, "status": "MISSING_IMDB_ID"}
        if not self.provider:
            return status | {"movie_id": movie_id, "imdb_id": external_id.external_id, "updated": False, "status": "NOT_CONFIGURED"}
        record = ensure_pending_rating(self.db, movie_id)
        now = datetime.now(timezone.utc)
        try:
            result = self.provider.fetch(external_id.external_id)
            if result is None:
                mark_rating_failure(record, movie, RATING_INVALID_ID, "Invalid IMDb identifier", now)
            else:
                apply_rating_result(record, movie, result)
            self.db.commit()
        except (ProviderRateLimited, ProviderQuotaExhausted) as exc:
            mark_rating_failure(record, movie, RATING_BLOCKED_BY_QUOTA, exc, now)
            self.db.commit()
        except Exception as exc:
            mark_rating_failure(record, movie, RATING_TEMPORARY_FAILURE, exc, now)
            self.db.commit()
        return status | {
            "movie_id": movie_id,
            "imdb_id": external_id.external_id,
            "updated": record.status == RATING_AVAILABLE,
            "status": record.status,
            "rating": record.rating,
            "vote_count": record.vote_count,
            "attempt_count": record.attempt_count,
            "last_error": record.last_error,
        }

    def health(self) -> dict:
        status = self.configuration_status()
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        latest = self.db.query(MovieRating).filter(
            func.lower(MovieRating.source) == "imdb",
            MovieRating.last_attempt_at.is_not(None),
        ).order_by(MovieRating.last_attempt_at.desc()).first()
        return status | {
            "status": (state.status if state else "NOT_RUN") if status["configured"] else "NOT_CONFIGURED",
            "last_success_at": state.last_success_at.isoformat() if state and state.last_success_at else None,
            "last_failure_at": state.last_failure_at.isoformat() if state and state.last_failure_at else None,
            "last_error": state.last_error if state else None,
            "last_request_at": latest.last_attempt_at.isoformat() if latest and latest.last_attempt_at else None,
            "last_rating_status": latest.status if latest else None,
        }

    def refresh(self, batch_size: int = 25) -> dict:
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if not state:
            state = OperationState(name=self.operation)
            self.db.add(state)
            self.db.flush()
        known_ids = self.db.query(func.count(func.distinct(ExternalId.movie_id))).filter(
            func.lower(ExternalId.provider) == "imdb"
        ).scalar() or 0
        state.total_count = known_ids
        if not self.provider:
            state.status = "BLOCKED"
            state.last_error = "IMDb rating provider is not configured"
            self.db.commit()
            return {"configured": False, "processed": 0, "updated": 0, "cursor": state.cursor, "complete": False}

        now = datetime.now(timezone.utc)
        today = date.today()
        rating = aliased(MovieRating)
        requested = exists(
            select(MovieRequest.id).where(
                MovieRequest.external_movie_id == Movie.tmdb_id,
                MovieRequest.status.in_(("PENDING", "REVIEWING", "FOUND")),
            )
        )
        batch_limit = max(1, min(batch_size, 100))
        query = self.db.query(Movie, ExternalId, rating).join(
            ExternalId,
            (ExternalId.movie_id == Movie.id) & (func.lower(ExternalId.provider) == "imdb"),
        ).outerjoin(
            rating,
            (rating.movie_id == Movie.id) & (func.lower(rating.source) == "imdb"),
        ).filter(
            or_(
                rating.id.is_(None),
                (rating.status.in_(tuple(RETRYABLE_RATING_STATES) + (RATING_AVAILABLE,)))
                & ((rating.next_check_at.is_(None)) | (rating.next_check_at <= now)),
            )
        ).order_by(
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
        ).limit(batch_limit)

        processed = updated = 0
        stopped = None
        rows = query.all()
        if not rows:
            state.status = "IDLE"
            if self.operation == "ratings.imdb_backfill":
                state.processed_count = self.db.query(func.count(func.distinct(MovieRating.movie_id))).filter(
                    func.lower(MovieRating.source) == "imdb",
                    MovieRating.last_attempt_at.is_not(None),
                ).scalar() or 0
            state.last_success_at = now
            state.last_error = None
            self.db.commit()
            return {
                "configured": True,
                "processed": 0,
                "updated": 0,
                "cursor": state.cursor,
                "complete": True,
                "stopped": None,
            }
        state.status = "RUNNING"
        for movie, external_id, record in rows:
            record = record or ensure_pending_rating(self.db, movie.id)
            if not IMDB_ID.fullmatch(external_id.external_id or ""):
                mark_rating_failure(record, movie, RATING_INVALID_ID, "Invalid IMDb identifier", now)
                processed += 1
                state.cursor = movie.id
                continue
            try:
                result = self.provider.fetch(external_id.external_id)
                if result is None:
                    mark_rating_failure(record, movie, RATING_INVALID_ID, "Invalid IMDb identifier", now)
                else:
                    apply_rating_result(record, movie, result)
                    updated += int(result.status == RATING_AVAILABLE)
                processed += 1
                state.cursor = movie.id
            except (ProviderRateLimited, ProviderQuotaExhausted) as exc:
                mark_rating_failure(record, movie, RATING_BLOCKED_BY_QUOTA, exc, now)
                state.status = "BLOCKED"
                state.last_failure_at = now
                state.last_error = sanitize_error(exc)
                stopped = "quota_or_rate_limit"
                break
            except Exception as exc:
                mark_rating_failure(record, movie, RATING_TEMPORARY_FAILURE, exc, now)
                state.status = "PAUSED"
                state.last_failure_at = now
                state.last_error = sanitize_error(exc)
                stopped = "provider_unavailable"
                break

        if self.operation == "ratings.imdb_backfill":
            state.processed_count = self.db.query(func.count(func.distinct(MovieRating.movie_id))).filter(
                func.lower(MovieRating.source) == "imdb",
                MovieRating.last_attempt_at.is_not(None),
            ).scalar() or 0
        else:
            state.processed_count += processed
        if not stopped:
            state.status = "IDLE"
            state.last_success_at = now
            state.last_error = None
        self.db.commit()
        return {
            "configured": True,
            "processed": processed,
            "updated": updated,
            "cursor": state.cursor,
            "complete": not stopped and len(rows) < batch_limit,
            "stopped": stopped,
        }
