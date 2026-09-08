"""Optional YouTube Data API trailer discovery and key health checks.

Only active when ``YOUTUBE_API_KEY`` is configured. Used as a last-resort
fallback for studios that do not publish their trailers on TMDB, plus a health
check that verifies stored keys still exist. Never hard-codes keys, never
parses YouTube HTML, and never downloads or re-hosts trailer content.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re

import httpx

from app.config.settings import settings
from app.models.movie import Movie

_JUNK = re.compile(
    r"reaction|fan[- ]?made|fan[- ]?trailer|review|explained|full movie|full film|"
    r"watch online|torrent|soundtrack|audio jukebox|juke ?box|music video",
    re.IGNORECASE,
)

STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "in", "on", "at", "with", "for", "from",
    "official", "trailer", "teaser", "movie", "film", "telugu", "tamil", "malayalam",
    "hindi", "kannada", "full", "hd", "2024", "2025", "2026", "2023", "2022",
}

_LANGUAGE_TAGS = {
    "hi": "hi", "ta": "ta", "te": "te", "ml": "ml", "kn": "kn", "bn": "bn",
    "mr": "mr", "gu": "gu", "pa": "pa", "ur": "ur", "en": "en",
}


def _title_fragments(title: str | None) -> list[str]:
    if not title:
        return []
    words = re.findall(r"[a-z0-9]+", title.lower())
    significant = [w for w in words if len(w) >= 4 and w not in STOPWORDS]
    return significant or words


def accepted_title(title: str | None, movie: Movie) -> bool:
    """Strict relevance: title references the movie and is a trailer/teaser."""
    if not title:
        return False
    if _JUNK.search(title):
        return False
    lowered = title.lower()
    if not re.search(r"\b(trailer|teaser)\b", lowered):
        return False
    fragments = _title_fragments(movie.title)
    if not fragments:
        return False
    if movie.title and movie.title.lower() in lowered:
        return True
    matched = sum(1 for frag in fragments if frag in lowered)
    return matched >= 2 or (len(fragments) == 1 and fragments[0] in lowered)


_LANGUAGE_NAMES = {
    "tamil": "ta",
    "telugu": "te",
    "malayalam": "ml",
    "hindi": "hi",
    "kannada": "kn",
    "bengali": "bn",
    "marathi": "mr",
    "gujarati": "gu",
    "punjabi": "pa",
    "urdu": "ur",
    "english": "en",
}


def _youtube_payload_for(key: str, title: str, published_at: str | None) -> dict:
    lowered = title.lower()
    language = next(
        (code for name, code in _LANGUAGE_NAMES.items() if re.search(rf"\b{name}\b", lowered)),
        None,
    )
    return {
        "site": "YouTube",
        "key": key,
        "type": "Trailer",
        "name": title,
        "official": False,
        "iso_639_1": language,
        "iso_3166_1": "IN",
        "published_at": published_at,
        "size": 720,
    }


class YouTubeTrailerService:
    """Thin YouTube Data API wrapper; disabled unless YOUTUBE_API_KEY is set."""

    BASE_URL = "https://www.googleapis.com/youtube/v3"

    def _configured(self) -> bool:
        return bool(settings.YOUTUBE_API_KEY)

    def search(self, movie: Movie, limit: int = 3) -> list[dict]:
        """Search for likely-official trailers when TMDB has no videos."""
        if not self._configured():
            return []
        query = f'"{movie.title}" trailer'
        params = {
            "part": "snippet",
            "type": "video",
            "videoEmbeddable": "true",
            "maxResults": 10,
            "q": query,
            "key": settings.YOUTUBE_API_KEY,
        }
        if movie.original_language in _LANGUAGE_TAGS:
            params["relevanceLanguage"] = _LANGUAGE_TAGS[movie.original_language]
        try:
            response = httpx.get(f"{self.BASE_URL}/search", params=params, timeout=20.0)
            if response.status_code in (400, 403):
                return []
            response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return []
        results: list[dict] = []
        for item in response.json().get("items", []):
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId")
            title = (snippet.get("title") or "").strip()
            if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                continue
            if not accepted_title(title, movie):
                continue
            results.append(
                _youtube_payload_for(
                    video_id,
                    title,
                    snippet.get("publishedAt"),
                )
            )
            if len(results) >= limit:
                break
        return results

    def health_check(self, video_key: str) -> bool | None:
        """Verify a stored YouTube key is still available; None when unconfigured."""
        if not self._configured() or not video_key:
            return None
        try:
            response = httpx.get(
                f"{self.BASE_URL}/videos",
                params={"part": "id", "id": video_key, "key": settings.YOUTUBE_API_KEY},
                timeout=20.0,
            )
            if response.status_code in (400, 403, 500):
                return None
            response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return None
        items = response.json().get("items", [])
        return bool(items)


def youtube_trailer_payload(value: dict) -> dict | None:
    """Normalize a YouTube search result into a URL-safe embed-friendly record."""
    published = value.get("published_at")
    if isinstance(published, str):
        try:
            published = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            published = None
    return {
        "provider": "YouTube",
        "video_key": value.get("key", ""),
        "video_type": value.get("type", "Trailer"),
        "name": value.get("name", ""),
        "official": bool(value.get("official", False)),
        "language": value.get("iso_639_1"),
        "country": value.get("iso_3166_1"),
        "size": value.get("size"),
        "published_at": published,
        "embed_url": f"https://www.youtube-nocookie.com/embed/{value.get('key', '')}",
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)