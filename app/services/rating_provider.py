"""Configurable, lawful IMDb-compatible rating retrieval and refresh."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re

import httpx
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, aliased

from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieRating
from app.models.operations import OperationState


IMDB_ID = re.compile(r"tt\d{7,10}")


class ProviderRateLimited(RuntimeError):
    """Provider asked the caller to stop the current batch and resume later."""


class ProviderQuotaExhausted(RuntimeError):
    """Provider account has no remaining quota for the current period."""


@dataclass(frozen=True)
class RatingResult:
    rating: float | None
    vote_count: int | None
    source_id: str
    checked_at: datetime


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
        response = httpx.get(
            self.api_url,
            params={"apikey": self.api_key, "i": imdb_id},
            timeout=self.timeout,
            follow_redirects=True,
        )
        if getattr(response, "status_code", 200) == 429:
            raise ProviderRateLimited("OMDb rate limit reached")
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("Response", "True")).lower() == "false" and any(
            phrase in str(payload.get("Error", "")).lower() for phrase in ("limit", "quota", "too many")
        ):
            raise ProviderQuotaExhausted(str(payload.get("Error"))[:300])
        checked_at = datetime.now(timezone.utc)
        if str(payload.get("Response", "True")).lower() == "false":
            return RatingResult(None, None, imdb_id, checked_at)
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
        return RatingResult(rating, vote_count, imdb_id, checked_at)


def configured_rating_provider() -> MovieRatingProvider | None:
    provider = settings.IMDB_RATING_PROVIDER.strip().lower()
    if provider == "omdb" and settings.IMDB_RATING_API_URL and settings.IMDB_RATING_API_KEY:
        return OmdbRatingProvider(settings.IMDB_RATING_API_URL, settings.IMDB_RATING_API_KEY)
    return None


class IMDbRatingRefreshService:
    """Refresh a small due batch, prioritizing missing, recent and popular titles."""

    def __init__(self, db: Session, provider: MovieRatingProvider | None = None):
        self.db = db
        self.provider = provider if provider is not None else configured_rating_provider()

    def refresh(self, batch_size: int = 25) -> dict:
        state = self.db.query(OperationState).filter_by(name="imdb_rating_refresh").first()
        if not state:
            state = OperationState(name="imdb_rating_refresh")
            self.db.add(state)
            self.db.flush()
        if not self.provider:
            state.last_error = "IMDb rating provider is not configured"
            self.db.commit()
            return {"configured": False, "processed": 0, "updated": 0, "cursor": state.cursor}

        now = datetime.now(timezone.utc)
        today = date.today()
        rating = aliased(MovieRating)
        query = self.db.query(Movie, ExternalId, rating).join(
            ExternalId,
            (ExternalId.movie_id == Movie.id) & (func.lower(ExternalId.provider) == "imdb"),
        ).outerjoin(
            rating,
            (rating.movie_id == Movie.id) & (func.lower(rating.source) == "imdb"),
        ).filter(
            or_(
                rating.id.is_(None),
                rating.last_updated_at.is_(None),
                (Movie.release_date >= today - timedelta(days=365)) & (rating.last_updated_at < now - timedelta(days=1)),
                (Movie.popularity >= 50) & (rating.last_updated_at < now - timedelta(days=3)),
                rating.last_updated_at < now - timedelta(days=30),
            )
        ).order_by(
            case((rating.id.is_(None), 0), (Movie.release_date >= today - timedelta(days=365), 1), (Movie.popularity >= 50, 2), else_=3),
            Movie.popularity.desc(),
            Movie.id,
        ).limit(max(1, min(batch_size, 100)))

        processed = updated = 0
        for movie, external_id, record in query.all():
            result = self.provider.fetch(external_id.external_id)
            processed += 1
            state.cursor = movie.id
            state.processed_count += 1
            if result is None:
                continue
            if record is None:
                record = MovieRating(movie_id=movie.id, source="IMDb")
                self.db.add(record)
            else:
                record.source = "IMDb"
            record.rating = result.rating
            record.vote_count = result.vote_count
            record.last_updated_at = result.checked_at
            updated += 1
        state.last_success_at = now
        state.last_error = None
        self.db.commit()
        return {"configured": True, "processed": processed, "updated": updated, "cursor": state.cursor}
