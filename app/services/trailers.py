"""Validated YouTube trailer selection and persistence.

Supports Trailer/Teaser/Featurette/Clip ingestion, deterministic ranking that
prefers official original-language trailers, junk-name filtering, and soft
unavailability tracking so dead YouTube keys are demoted without data loss.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re

from sqlalchemy.orm import Session

from app.models.movie import Movie
from app.models.movie_metadata import MovieTrailer


YOUTUBE_KEY = re.compile(r"^[A-Za-z0-9_-]{11}$")

INDIAN_LANGUAGES = {
    "hi", "ta", "te", "ml", "kn", "bn", "mr", "gu", "pa", "ur", "or",
    "as", "sa", "ne", "si",
}

_JUNK_PATTERNS = (
    r"reaction",
    r"fan[- ]?made",
    r"fan[- ]?trailer",
    r"review",
    r"explained",
    r"full movie",
    r"full film",
    r"watch online",
    r"torrent",
    r"soundtrack",
    r"audio jukebox",
    r"juke ?box",
    r"music video",
)
JUNK_NAME_RE = re.compile("|".join(_JUNK_PATTERNS), re.IGNORECASE)

_LABEL_TYPES = {"trailer": 0, "teaser": 1, "featurette": 2, "clip": 3}

_LABEL_NAMES = {
    "trailer": "Trailer",
    "teaser": "Teaser",
    "featurette": "Featurette",
    "clip": "Clip",
    "behind the scenes": "Behind The Scenes",
    "behindthescenes": "Behind The Scenes",
    "bloopers": "Bloopers",
}


def valid_youtube_key(value: str | None) -> bool:
    return bool(value and YOUTUBE_KEY.fullmatch(value))


def junk_trailer_name(name: str | None) -> bool:
    return bool(name and JUNK_NAME_RE.search(name))


def _normalized_type(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    label = value.strip().lower()
    if label in _LABEL_TYPES:
        return label
    canonical = _LABEL_NAMES.get(label)
    if canonical:
        return canonical.lower()
    return label


def _language_rank(language: str | None, original_language: str | None) -> int:
    lang = (language or "").lower()
    original = (original_language or "").lower()
    if original and lang == original:
        return 0
    if lang in INDIAN_LANGUAGES:
        return 1
    if lang == "en":
        return 2
    return 3


def _published_timestamp(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def trailer_score(item: MovieTrailer, original_language: str | None = None) -> tuple:
    """Deterministic preference tuple for ranking.

    Order of preference: official trailer -> official teaser -> unofficial
    trailer -> other official video -> other unofficial video, then language
    (original > other Indian > English > other), then official-trailer naming,
    then earliest published_at, then lowest id.
    """
    video_type = _normalized_type(item.video_type)
    label = _LABEL_TYPES.get(video_type or "", 3)
    official = bool(item.official)
    if label == 0:  # trailer
        class_rank = 0 if official else 2
    elif label == 1:  # teaser
        class_rank = 1 if official else 4
    else:  # featurette / clip / behind-the-scenes
        class_rank = 3 if official else 5

    name = (item.name or "").lower()
    name_quality = int(label == 0 and "official trailer" in name)
    published = _published_timestamp(item.published_at)
    return (
        -class_rank,
        -_language_rank(item.language, original_language),
        name_quality,
        -published if published else float("-inf"),
        -item.id,
    )


def ranked_trailers(movie: Movie, *, include_unavailable: bool = False) -> list[MovieTrailer]:
    """Stored trailers sorted best-first, exclusding junk and dead video keys."""
    candidates = [
        item
        for item in movie.trailers
        if item.provider.lower() == "youtube"
        and valid_youtube_key(item.video_key)
        and not junk_trailer_name(item.name)
        and (include_unavailable or not item.is_unavailable)
    ]
    return sorted(candidates, key=lambda item: trailer_score(item, movie.original_language), reverse=True)


def trailer_payload(item: MovieTrailer | None) -> dict | None:
    if not item or item.provider.lower() != "youtube" or not valid_youtube_key(item.video_key):
        return None
    return {
        "provider": "YouTube",
        "video_key": item.video_key,
        "video_type": item.video_type,
        "name": item.name,
        "official": item.official,
        "language": item.language,
        "country": item.country,
        "size": item.size,
        "published_at": item.published_at,
        "embed_url": f"https://www.youtube-nocookie.com/embed/{item.video_key}",
    }


def trailer_videos_payload(movie: Movie, limit: int = 6) -> list[dict]:
    """Best available videos for a movie, ready for the public detail payload."""
    payloads: list[dict] = []
    for item in ranked_trailers(movie, include_unavailable=False):
        payload = trailer_payload(item)
        if payload:
            payloads.append(payload)
        if len(payloads) >= limit:
            break
    return payloads


class TrailerService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _published(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    def _store(self, movie: Movie, value: dict, now: datetime, *, current_keys: set[str]) -> None:
        key = (value.get("key") or "").strip()
        site = (value.get("site") or "").lower()
        video_type = _normalized_type(value.get("type"))
        if (
            site != "youtube"
            or not video_type
            or not valid_youtube_key(key)
            or junk_trailer_name(value.get("name"))
        ):
            return
        current_keys.add(key)
        item = (
            self.db.query(MovieTrailer)
            .filter_by(movie_id=movie.id, provider="YouTube", video_key=key)
            .first()
        )
        if not item:
            item = MovieTrailer(movie_id=movie.id, provider="YouTube", video_key=key)
            self.db.add(item)
            self.db.flush()
        was_unavailable = item.is_unavailable
        item.video_type = _LABEL_NAMES.get(video_type, video_type)
        item.name = (value.get("name") or "Trailer").strip()[:500]
        item.official = bool(value.get("official", False))
        item.language = (value.get("iso_639_1") or "").strip() or None
        item.country = (value.get("iso_3166_1") or "").strip() or None
        size = value.get("size")
        item.size = int(size) if isinstance(size, int) else (int(size) if str(size).isdigit() else None)
        item.published_at = self._published(value.get("published_at")) or item.published_at
        item.last_verified_at = now
        item.last_checked_at = now
        if was_unavailable:
            item.is_unavailable = False

    def upsert(self, movie: Movie, data: dict, *, commit: bool = False) -> MovieTrailer | None:
        now = datetime.now(timezone.utc)
        results = data.get("results")
        current_keys: set[str] = set()
        for value in results or []:
            self._store(movie, value, now, current_keys=current_keys)

        if isinstance(results, list) and results:
            for stored in self.db.query(MovieTrailer).filter_by(movie_id=movie.id).all():
                if stored.provider.lower() != "youtube" or not valid_youtube_key(stored.video_key):
                    continue
                if stored.video_key not in current_keys and not stored.is_unavailable:
                    stored.is_unavailable = True
                    stored.last_checked_at = now

        primary = next(iter(ranked_trailers(movie)), None)
        for item in movie.trailers:
            item.is_primary = item is primary
        if commit:
            self.db.commit()
            if primary:
                self.db.refresh(primary)
        return primary

    def primary(self, movie_id: int) -> MovieTrailer | None:
        movie = self.db.get(Movie, movie_id)
        candidates = self.db.query(MovieTrailer).filter_by(movie_id=movie_id).all()
        valid = [
            item
            for item in candidates
            if item.provider.lower() == "youtube"
            and valid_youtube_key(item.video_key)
            and not item.is_unavailable
            and not junk_trailer_name(item.name)
        ]
        return max(
            valid,
            key=lambda item: trailer_score(item, movie.original_language if movie else None),
            default=None,
        )

    def rank(self, movie_id: int, limit: int = 6) -> list[MovieTrailer]:
        movie = self.db.get(Movie, movie_id)
        if not movie:
            return []
        return ranked_trailers(movie)[:limit]