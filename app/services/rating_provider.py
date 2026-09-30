"""Configurable, lawful IMDb-compatible rating retrieval and refresh."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import logging
import re
import threading
import time

import httpx
import redis
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
RATING_NEEDS_REVIEW = "NEEDS_REVIEW"
RATING_PROVIDER_DATA_UNAVAILABLE = "PROVIDER_DATA_UNAVAILABLE"
RETRYABLE_RATING_STATES = {
    RATING_PENDING,
    RATING_NOT_YET_RATED,
    RATING_TEMPORARY_FAILURE,
    RATING_NOT_FOUND,
    RATING_BLOCKED_BY_QUOTA,
}
# Rows in these states carry a verified rating or an unresolved research finding.
# Provider failures and empty responses must never replace them.
PROTECTED_RATING_STATUSES = {
    RATING_AVAILABLE,
    RATING_NEEDS_REVIEW,
    RATING_PROVIDER_DATA_UNAVAILABLE,
}
RESEARCH_RATING_STATUSES = {
    RATING_NEEDS_REVIEW,
    RATING_PROVIDER_DATA_UNAVAILABLE,
}

logger = logging.getLogger(__name__)

OMDB_QUOTA_COOLDOWN_SECONDS = 6 * 60 * 60
OMDB_RATE_LIMIT_COOLDOWN_SECONDS = 5 * 60
OMDB_REDIS_PREFIX = "omdb:key"

_omdb_state_lock = threading.RLock()
_local_quota_until: dict[str, float] = {}
_local_invalid_keys: set[str] = set()
_last_redis_warning_at = 0.0


class ProviderRateLimited(RuntimeError):
    """Provider asked the caller to stop the current batch and resume later."""


class ProviderQuotaExhausted(RuntimeError):
    """Provider account has no remaining quota for the current period."""


class ProviderUnavailable(RuntimeError):
    """Provider is temporarily unavailable; the batch must pause cleanly."""


class ProviderInvalidKey(RuntimeError):
    """A configured provider credential is invalid for the current process."""


class ProviderTimeout(ProviderUnavailable):
    """The provider timed out; other credentials must not be consumed."""


class ProviderMalformedResponse(ProviderUnavailable):
    """The provider returned a response that could not be interpreted safely."""


class OmdbResponseClass(str, Enum):
    SUCCESS = "SUCCESS"
    NOT_FOUND = "NOT_FOUND"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_KEY = "INVALID_KEY"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"


def omdb_key_fingerprint(api_key: str) -> str:
    """Return a stable, irreversible identifier safe for state and logs."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]


def _warn_omdb_redis_unavailable(exc: Exception) -> None:
    global _last_redis_warning_at
    now = time.monotonic()
    with _omdb_state_lock:
        if now - _last_redis_warning_at < 60:
            return
        _last_redis_warning_at = now
    logger.warning(
        "OMDb Redis coordination unavailable; using in-process fallback (%s)",
        type(exc).__name__,
    )


class OmdbKeyStateStore:
    """Coordinate OMDb credential cooldown without storing raw credentials."""

    def __init__(self, redis_client=None):
        self._redis_client = redis_client

    def _redis(self):
        if self._redis_client is None:
            self._redis_client = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
        return self._redis_client

    @staticmethod
    def _quota_key(fingerprint: str) -> str:
        return f"{OMDB_REDIS_PREFIX}:{fingerprint}:quota_exhausted"

    def is_temporarily_unavailable(self, fingerprint: str) -> bool:
        now = time.time()
        with _omdb_state_lock:
            local_until = _local_quota_until.get(fingerprint, 0)
            if local_until > now:
                return True
            _local_quota_until.pop(fingerprint, None)
        try:
            return bool(self._redis().exists(self._quota_key(fingerprint)))
        except Exception as exc:
            _warn_omdb_redis_unavailable(exc)
            return False

    def mark_temporarily_unavailable(self, fingerprint: str, ttl_seconds: int) -> None:
        ttl_seconds = max(1, int(ttl_seconds))
        with _omdb_state_lock:
            _local_quota_until[fingerprint] = time.time() + ttl_seconds
        try:
            self._redis().setex(self._quota_key(fingerprint), ttl_seconds, "1")
        except Exception as exc:
            _warn_omdb_redis_unavailable(exc)

    @staticmethod
    def is_invalid(fingerprint: str) -> bool:
        with _omdb_state_lock:
            return fingerprint in _local_invalid_keys

    @staticmethod
    def mark_invalid(fingerprint: str) -> None:
        with _omdb_state_lock:
            _local_invalid_keys.add(fingerprint)


def _reset_omdb_key_state_for_tests() -> None:
    """Reset process-local provider state between isolated tests."""
    global _last_redis_warning_at
    with _omdb_state_lock:
        _local_quota_until.clear()
        _local_invalid_keys.clear()
        _last_redis_warning_at = 0.0


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
    """Fetch IMDb-compatible rating fields through a configured OMDb API account.

    Supports a key pool for automatic rotation when a single key is rate-limited
    or quota-exhausted.  Keys are tried in order; ``ProviderQuotaExhausted`` is
    raised only when *every* key in the pool is exhausted for this request.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        *,
        key_pool: list[str] | None = None,
        timeout: float = 15,
        state_store: OmdbKeyStateStore | None = None,
    ):
        self.api_url = api_url
        self.timeout = timeout
        self._state = state_store or OmdbKeyStateStore()
        # Deduplicated key pool: primary key first, then any extras
        seen: set[str] = set()
        self._keys: list[str] = []
        for candidate in [api_key, *(key_pool or [])]:
            key = (candidate or "").strip()
            if key and key not in seen:
                self._keys.append(key)
                seen.add(key)

    # ── Single-key fetch (original logic) ──────────────────────────────

    def _fetch_one(self, imdb_id: str, api_key: str) -> RatingResult | None:
        """Attempt a single OMDb lookup with *api_key*."""
        try:
            response = httpx.get(
                self.api_url,
                params={"apikey": api_key, "i": imdb_id},
                timeout=self.timeout,
                follow_redirects=True,
            )
        except httpx.TimeoutException:
            raise ProviderTimeout("OMDb request timed out") from None
        except httpx.HTTPError:
            raise ProviderUnavailable("OMDb transport unavailable") from None
        status_code = getattr(response, "status_code", 200)
        if status_code == 429:
            raise ProviderRateLimited("IMDb rating provider rate limit reached")
        if status_code in {401, 403}:
            raise ProviderInvalidKey("OMDb credential is invalid")
        if status_code == 402:
            raise ProviderQuotaExhausted("OMDb credential quota is exhausted")
        if status_code >= 500:
            raise ProviderUnavailable("IMDb rating provider is temporarily unavailable")
        if status_code >= 400:
            raise ProviderUnavailable(f"IMDb rating provider returned HTTP {status_code}")

        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise ProviderMalformedResponse("OMDb returned malformed JSON") from None
        if not isinstance(payload, dict):
            raise ProviderMalformedResponse("OMDb returned an unexpected response shape")

        response_class = self._classify_payload(payload)
        if response_class == OmdbResponseClass.QUOTA_EXHAUSTED:
            raise ProviderQuotaExhausted("OMDb credential quota is exhausted")
        if response_class == OmdbResponseClass.RATE_LIMITED:
            raise ProviderRateLimited("OMDb credential is rate limited")
        if response_class == OmdbResponseClass.INVALID_KEY:
            raise ProviderInvalidKey("OMDb credential is invalid")
        if response_class == OmdbResponseClass.PROVIDER_UNAVAILABLE:
            raise ProviderUnavailable("OMDb returned an unclassified provider error")
        if response_class == OmdbResponseClass.MALFORMED_RESPONSE:
            raise ProviderMalformedResponse("OMDb response is missing required status data")

        checked_at = datetime.now(timezone.utc)
        if response_class == OmdbResponseClass.NOT_FOUND:
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

    @staticmethod
    def _classify_payload(payload: dict) -> OmdbResponseClass:
        response_value = str(payload.get("Response", "")).strip().lower()
        if response_value == "true":
            return OmdbResponseClass.SUCCESS
        if response_value != "false":
            return OmdbResponseClass.MALFORMED_RESPONSE

        error = str(payload.get("Error", "")).strip().lower()
        if any(phrase in error for phrase in ("invalid api key", "no api key provided", "api key is invalid")):
            return OmdbResponseClass.INVALID_KEY
        if any(phrase in error for phrase in ("too many requests", "rate limit", "rate-limit")):
            return OmdbResponseClass.RATE_LIMITED
        if any(phrase in error for phrase in ("request limit reached", "daily limit", "quota", "limit exhausted")):
            return OmdbResponseClass.QUOTA_EXHAUSTED
        if any(phrase in error for phrase in ("movie not found", "title not found", "incorrect imdb id", "not found")):
            return OmdbResponseClass.NOT_FOUND
        return OmdbResponseClass.PROVIDER_UNAVAILABLE

    # ── Multi-key fetch with rotation ──────────────────────────────────

    def fetch(self, imdb_id: str) -> RatingResult | None:
        if not IMDB_ID.fullmatch(imdb_id):
            return None
        last_retryable: Exception | None = None
        invalid_count = 0
        for key in self._keys:
            fingerprint = omdb_key_fingerprint(key)
            if self._state.is_invalid(fingerprint):
                invalid_count += 1
                continue
            if self._state.is_temporarily_unavailable(fingerprint):
                continue
            logger.info("OMDb key selected fingerprint=%s", fingerprint)
            try:
                result = self._fetch_one(imdb_id, key)
                logger.info(
                    "OMDb request completed fingerprint=%s status=%s",
                    fingerprint,
                    result.status if result else RATING_INVALID_ID,
                )
                return result
            except ProviderQuotaExhausted as exc:
                last_retryable = exc
                self._state.mark_temporarily_unavailable(fingerprint, OMDB_QUOTA_COOLDOWN_SECONDS)
                logger.warning("OMDb key quota exhausted fingerprint=%s; failing over", fingerprint)
            except ProviderRateLimited as exc:
                last_retryable = exc
                self._state.mark_temporarily_unavailable(fingerprint, OMDB_RATE_LIMIT_COOLDOWN_SECONDS)
                logger.warning("OMDb key rate limited fingerprint=%s; failing over", fingerprint)
            except ProviderInvalidKey:
                invalid_count += 1
                self._state.mark_invalid(fingerprint)
                logger.warning("OMDb key invalid fingerprint=%s; skipping for this process", fingerprint)
            except (ProviderTimeout, ProviderMalformedResponse, ProviderUnavailable):
                # These failures are provider-wide or ambiguous. Trying every key
                # would duplicate load without establishing a credential problem.
                raise
        logger.error("All OMDb credentials unavailable configured=%d", len(self._keys))
        if last_retryable:
            raise ProviderQuotaExhausted("All OMDb credentials are temporarily unavailable") from None
        if invalid_count:
            raise ProviderInvalidKey("All OMDb credentials are invalid or unavailable") from None
        raise ProviderQuotaExhausted("All OMDb credentials are temporarily unavailable")


def _parse_omdb_keys() -> list[str]:
    """Return deduplicated OMDb API keys from OMDB_API_KEYS, falling back to IMDB_RATING_API_KEY."""
    def parse(raw_source: str) -> list[str]:
        pool: list[str] = []
        seen: set[str] = set()
        for candidate in (raw_source or "").split(","):
            key = candidate.strip()
            if key and key not in seen:
                pool.append(key)
                seen.add(key)
        return pool

    configured_pool = parse(settings.OMDB_API_KEYS)
    return configured_pool or parse(settings.IMDB_RATING_API_KEY)


def configured_rating_provider() -> MovieRatingProvider | None:
    provider = settings.IMDB_RATING_PROVIDER.strip().lower()
    if provider != "omdb" or not settings.IMDB_RATING_API_URL:
        return None
    keys = _parse_omdb_keys()
    if not keys:
        return None
    return OmdbRatingProvider(settings.IMDB_RATING_API_URL, keys[0], key_pool=keys[1:])


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
    if (record.status or "") in RESEARCH_RATING_STATUSES:
        # Research is the source of truth while a finding is unresolved; do not
        # let a provider result resolve NEEDS_REVIEW or PROVIDER_DATA_UNAVAILABLE.
        record.last_attempt_at = result.checked_at
        record.last_error = f"Research state {record.status} protected; provider result ignored"
        record.attempt_count = (record.attempt_count or 0) + 1
        return
    if result.rating is None and record.rating is not None:
        # Provider returned no value (N/A, missing field); never discard an
        # existing verified rating. Surface the provider outcome in metadata only.
        record.last_attempt_at = result.checked_at
        record.last_error = "Provider returned no rating; preserved existing verified value"
        record.attempt_count = (record.attempt_count or 0) + 1
        record.next_check_at = next_rating_check(movie, record.status, record.attempt_count, result.checked_at)
        return
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
    record.last_attempt_at = now
    record.last_error = sanitize_error(error, limit=1000)
    record.attempt_count = (record.attempt_count or 0) + 1
    keep_state = record.rating is not None or (record.status or "") in PROTECTED_RATING_STATUSES
    if keep_state:
        # Provider failures are provider states, not movie-rating states. A
        # verified rating or an open NEEDS_REVIEW/PDU finding must be preserved.
        record.next_check_at = next_rating_check(movie, record.status, record.attempt_count, now)
        return
    record.status = status
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
        if not _parse_omdb_keys():
            # Keep the established diagnostic contract. OMDB_API_KEYS is an
            # optional multi-key alternative; IMDB_RATING_API_KEY remains the
            # documented single-key configuration accepted by the provider.
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
            "last_rating_updated": latest.last_updated_at.isoformat() if latest and latest.last_updated_at else None,
            "requests_today": self.db.query(MovieRating).filter(
                func.lower(MovieRating.source) == "imdb",
                MovieRating.last_attempt_at >= datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc),
            ).count(),
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
        batch_limit = max(1, min(batch_size, 500))
        query = self.db.query(Movie, ExternalId, rating).join(
            ExternalId,
            (ExternalId.movie_id == Movie.id) & (func.lower(ExternalId.provider) == "imdb"),
        ).outerjoin(
            rating,
            (rating.movie_id == Movie.id) & (func.lower(rating.source) == "imdb"),
        ).filter(
            or_(
                rating.id.is_(None),
                (rating.status.in_(tuple(RETRYABLE_RATING_STATES)))
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
                self.db.commit()
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
                # A 500-item provider batch can run for several minutes. Persist
                # each completed attempt so a worker/container restart resumes
                # after the last durable movie instead of replaying the batch.
                self.db.commit()
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
