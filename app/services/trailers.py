"""Validated YouTube trailer selection and persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from sqlalchemy.orm import Session

from app.models.movie import Movie
from app.models.movie_metadata import MovieTrailer


YOUTUBE_KEY = re.compile(r"^[A-Za-z0-9_-]{11}$")


def valid_youtube_key(value: str | None) -> bool:
    return bool(value and YOUTUBE_KEY.fullmatch(value))


def trailer_score(item: MovieTrailer, original_language: str | None = None) -> tuple:
    name = (item.name or "").lower()
    video_type = (item.video_type or "").lower()
    return (
        int(item.official) * 100,
        int(video_type == "trailer") * 50,
        int(bool(original_language) and item.language == original_language) * 30,
        int("official trailer" in name) * 25,
        int(item.language == "en") * 5,
        item.published_at or datetime.min.replace(tzinfo=timezone.utc),
        -item.id,
    )


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
        "published_at": item.published_at,
        "embed_url": f"https://www.youtube-nocookie.com/embed/{item.video_key}",
    }


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

    def upsert(self, movie: Movie, data: dict, *, commit: bool = False) -> MovieTrailer | None:
        now = datetime.now(timezone.utc)
        for value in data.get("results", []):
            key = (value.get("key") or "").strip()
            if (
                (value.get("site") or "").lower() != "youtube"
                or (value.get("type") or "").lower() != "trailer"
                or not valid_youtube_key(key)
            ):
                continue
            item = self.db.query(MovieTrailer).filter_by(
                movie_id=movie.id, provider="YouTube", video_key=key
            ).first()
            if not item:
                item = MovieTrailer(movie_id=movie.id, provider="YouTube", video_key=key)
                self.db.add(item)
                self.db.flush()
            item.video_type = value.get("type") or "Trailer"
            item.name = (value.get("name") or "Trailer").strip()[:500]
            item.official = bool(value.get("official", False))
            item.language = (value.get("iso_639_1") or None)
            item.published_at = self._published(value.get("published_at"))
            item.last_verified_at = now

        valid = [
            item for item in self.db.query(MovieTrailer).filter_by(movie_id=movie.id).all()
            if item.provider.lower() == "youtube" and valid_youtube_key(item.video_key)
        ]
        primary = max(valid, key=lambda item: trailer_score(item, movie.original_language), default=None)
        for item in valid:
            item.is_primary = item is primary
        if commit:
            self.db.commit()
            if primary:
                self.db.refresh(primary)
        return primary

    def primary(self, movie_id: int) -> MovieTrailer | None:
        movie = self.db.get(Movie, movie_id)
        candidates = self.db.query(MovieTrailer).filter_by(movie_id=movie_id).all()
        valid = [item for item in candidates if valid_youtube_key(item.video_key)]
        return max(valid, key=lambda item: trailer_score(item, movie.original_language if movie else None), default=None)
