"""Optional YouTube Data API trailer discovery and key health checks.

Only active when ``YOUTUBE_API_KEY`` is configured. Used as a last-resort
fallback for studios that do not publish their trailers on TMDB, plus a health
check that verifies stored keys still exist. Never hard-codes keys, never
parses YouTube HTML, and never downloads or re-hosts trailer content.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import time

import httpx

from app.config.settings import settings
from app.models.movie import Movie

_JUNK = re.compile(
    r"reaction|fan[- ]?made|fan[- ]?trailer|review|explained|full movie|full film|"
    r"ending explained|concept trailer|ai[- ]?generated|ai trailer|fake trailer|parody|"
    r"watch online|torrent|soundtrack|audio jukebox|juke ?box|music video|lyric(?:al)?|"
    r"\b(song|scene|clip|interview|making|behind the scenes|bts|shorts?)\b",
    re.IGNORECASE,
)

_LOW_VALUE_VIDEO = re.compile(r"\b(shorts?|clip|scene|song|lyric|teaser)\b", re.IGNORECASE)
_OFFICIAL_CHANNEL = re.compile(
    r"\b(official|studios?|pictures|films?|movies|entertainment|productions?|distribution|netflix|prime video|disney|sony|warner|universal|paramount)\b",
    re.IGNORECASE,
)


class YouTubeTrailerError(RuntimeError):
    """Base class for secret-safe provider failures."""


class YouTubeQuotaExceeded(YouTubeTrailerError):
    pass


class YouTubeInvalidKey(YouTubeTrailerError):
    pass


class YouTubeTemporaryError(YouTubeTrailerError):
    pass

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


def _duration_seconds(value: str | None) -> int | None:
    match = re.fullmatch(r"P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)", value or "")
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


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


def _youtube_payload_for(key: str, title: str, published_at: str | None, *, official: bool = False) -> dict:
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
        "official": official,
        "iso_639_1": language,
        "iso_3166_1": "IN",
        "published_at": published_at,
        "size": 720,
    }


class YouTubeTrailerService:
    """Thin YouTube Data API wrapper; disabled unless YOUTUBE_API_KEY is set."""

    BASE_URL = "https://www.googleapis.com/youtube/v3"

    def __init__(self):
        self.last_query: str | None = None
        self.last_candidate: dict | None = None

    @staticmethod
    def _error_reason(response: httpx.Response) -> str:
        try:
            errors = response.json().get("error", {}).get("errors", [])
            return str(errors[0].get("reason") or "") if errors else ""
        except (TypeError, ValueError):
            return ""

    def _request(self, path: str, params: dict) -> httpx.Response:
        for attempt in range(3):
            try:
                response = httpx.get(f"{self.BASE_URL}/{path}", params=params, timeout=20.0)
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise YouTubeTemporaryError("YouTube network request failed") from exc
                time.sleep(2 ** attempt)
                continue
            reason = self._error_reason(response)
            if response.status_code == 403 and reason in {"quotaExceeded", "dailyLimitExceeded"}:
                raise YouTubeQuotaExceeded("YouTube daily quota exhausted")
            if response.status_code in {400, 401, 403}:
                raise YouTubeInvalidKey("YouTube API credentials were rejected")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise YouTubeTemporaryError(f"YouTube temporary HTTP {response.status_code}")
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            return response
        raise YouTubeTemporaryError("YouTube request failed")

    @staticmethod
    def search_query(movie: Movie) -> str:
        year = movie.release_date.year if movie.release_date else None
        return " ".join(part for part in (movie.title, str(year) if year else None, "official trailer") if part)

    @staticmethod
    def _score(item: dict, movie: Movie) -> int:
        snippet = item.get("snippet") or {}
        title = (snippet.get("title") or "").strip()
        description = snippet.get("description") or ""
        channel = snippet.get("channelTitle") or ""
        if not accepted_title(title, movie):
            return -100
        score = 4
        lowered = f"{title} {description}".lower()
        if "official trailer" in lowered:
            score += 4
        if _OFFICIAL_CHANNEL.search(channel):
            score += 2
        if _LOW_VALUE_VIDEO.search(title):
            score -= 4
        if movie.release_date and str(movie.release_date.year) in lowered:
            score += 1
        if movie.release_date:
            years = {int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", lowered)}
            compatible = {movie.release_date.year - 1, movie.release_date.year, movie.release_date.year + 1}
            if years and not (years & compatible):
                return -100
        return score

    def _configured(self) -> bool:
        return bool(settings.YOUTUBE_API_KEY)

    def search(self, movie: Movie, limit: int = 3) -> list[dict]:
        """Search for likely-official trailers when TMDB has no videos."""
        if not self._configured():
            return []
        query = self.search_query(movie)
        self.last_query = query
        params = {
            "part": "snippet",
            "type": "video",
            "videoEmbeddable": "true",
            "maxResults": 5,
            "q": query,
            "key": settings.YOUTUBE_API_KEY,
        }
        if movie.original_language in _LANGUAGE_TAGS:
            params["relevanceLanguage"] = _LANGUAGE_TAGS[movie.original_language]
        response = self._request("search", params)
        ranked = sorted(response.json().get("items", []), key=lambda value: self._score(value, movie), reverse=True)
        results: list[dict] = []
        for item in ranked:
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId")
            title = (snippet.get("title") or "").strip()
            if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                continue
            score = self._score(item, movie)
            if score < 8:
                continue
            validation = self._request(
                "videos",
                {"part": "snippet,status,contentDetails", "id": video_id, "key": settings.YOUTUBE_API_KEY},
            ).json().get("items", [])
            if not validation or validation[0].get("status", {}).get("embeddable") is False:
                continue
            validated_title = (validation[0].get("snippet", {}).get("title") or title).strip()
            if not accepted_title(validated_title, movie):
                continue
            duration = _duration_seconds(validation[0].get("contentDetails", {}).get("duration"))
            if duration is not None and (duration < 15 or duration > 15 * 60):
                continue
            channel = snippet.get("channelTitle") or validation[0].get("snippet", {}).get("channelTitle") or ""
            official = "official trailer" in f"{title} {snippet.get('description') or ''}".lower() and bool(_OFFICIAL_CHANNEL.search(channel))
            results.append(
                _youtube_payload_for(
                    video_id,
                    title,
                    snippet.get("publishedAt"),
                    official=official,
                )
            )
            self.last_candidate = {"video_id": video_id, "title": title, "channel": channel, "score": score, "validated": True}
            if len(results) >= limit:
                break
        return results

    def health_check(self, video_key: str) -> bool | None:
        """Verify a stored YouTube key is still available; None when unconfigured."""
        if not self._configured() or not video_key:
            return None
        try:
            response = self._request("videos", {"part": "id,status", "id": video_key, "key": settings.YOUTUBE_API_KEY})
        except YouTubeTrailerError:
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
