"""Lawful, opt-in OTT research adapters and conservative evidence scoring."""

from __future__ import annotations

from datetime import date, datetime
import re
from urllib.parse import urlparse

import httpx

from app.config.settings import settings

OFFICIAL = ("netflix.com", "primevideo.com", "hotstar.com", "jiohotstar.com", "jiocinema.com", "sonyliv.com", "zee5.com", "aha.video", "sunnxt.com", "manoramamax.com", "mxplayer.in")
OFFICIAL_ANNOUNCEMENT = ("youtube.com", "instagram.com", "x.com", "facebook.com")
TRUSTED = ("variety.com", "hollywoodreporter.com", "deadline.com", "screendaily.com", "indiatoday.in", "thehindu.com", "indianexpress.com", "filmcompanion.in", "cinemaexpress.com")
PLATFORMS = {
    "netflix.com": "Netflix", "primevideo.com": "Amazon Prime Video", "hotstar.com": "JioHotstar",
    "jiohotstar.com": "JioHotstar", "jiocinema.com": "JioCinema", "sonyliv.com": "SonyLIV",
    "zee5.com": "ZEE5", "aha.video": "aha", "sunnxt.com": "Sun NXT", "manoramamax.com": "ManoramaMAX",
    "mxplayer.in": "Amazon MX Player",
}


def source_rank(url: str | None) -> tuple[str, float]:
    domain = (url or "").lower()
    if any(x in domain for x in OFFICIAL): return "official_platform", 95.0
    if any(x in domain for x in OFFICIAL_ANNOUNCEMENT): return "official_announcement_needs_identity_verification", 80.0
    if any(x in domain for x in TRUSTED): return "established_publication", 82.0
    return "unknown", 35.0


def _platform(url: str, text: str) -> str | None:
    domain = urlparse(url).netloc.lower()
    for key, name in PLATFORMS.items():
        if key in domain: return name
    lowered = text.lower()
    aliases = {"netflix": "Netflix", "prime video": "Amazon Prime Video", "amazon prime": "Amazon Prime Video", "jiohotstar": "JioHotstar", "hotstar": "JioHotstar", "sonyliv": "SonyLIV", "zee5": "ZEE5", "sun nxt": "Sun NXT", "sunnxt": "Sun NXT", "manoramamax": "ManoramaMAX", "aha": "aha"}
    for alias, name in aliases.items():
        if alias in lowered: return name
    return None


def _release_date(text: str) -> str | None:
    iso = re.search(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b", text)
    if iso:
        try: return date(int(iso[1]), int(iso[2]), int(iso[3])).isoformat()
        except ValueError: return None
    for fmt, pattern in (("%B %d, %Y", r"\b[A-Z][a-z]+ \d{1,2}, 20\d{2}\b"), ("%d %B %Y", r"\b\d{1,2} [A-Z][a-z]+ 20\d{2}\b")):
        found = re.search(pattern, text)
        if found:
            try: return datetime.strptime(found.group(), fmt).date().isoformat()
            except ValueError: pass
    return None


class GoogleProgrammableSearchProvider:
    """Google Custom Search JSON API adapter; it never scrapes Google HTML."""

    endpoint = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str | None = None, engine_id: str | None = None, timeout: float = 15):
        self.api_key = settings.GOOGLE_SEARCH_API_KEY if api_key is None else api_key
        self.engine_id = settings.GOOGLE_SEARCH_ENGINE_ID if engine_id is None else engine_id
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.engine_id)

    def search(self, movie) -> list[dict]:
        if not self.configured: return []
        title = movie.title if hasattr(movie, "title") else str(movie)
        year = getattr(getattr(movie, "release_date", None), "year", "")
        language = getattr(movie, "original_language", "") or ""
        identity = " ".join(str(x) for x in (f'"{title}"', year, language) if x)
        queries = (f"{identity} OTT release India", f"{identity} streaming Netflix Prime Video JioHotstar SonyLIV ZEE5", f"{identity} digital release date official")
        results: list[dict] = []; seen: set[str] = set()
        for query in queries:
            response = httpx.get(self.endpoint, params={"key": self.api_key, "cx": self.engine_id, "q": query, "num": 10}, timeout=self.timeout, follow_redirects=True)
            if response.status_code == 429: raise RuntimeError("Google Custom Search quota or rate limit reached")
            response.raise_for_status()
            for item in response.json().get("items", []):
                url = item.get("link")
                if not url or url in seen: continue
                seen.add(url)
                text = " ".join(filter(None, (item.get("title"), item.get("snippet"))))
                results.append({"title": item.get("title"), "url": url, "snippet": item.get("snippet", ""), "platform": _platform(url, text), "release_date": _release_date(text), "query": query})
        return results


class ConfiguredSearchProvider:
    """Adapter for a configured consented JSON search endpoint."""

    @property
    def configured(self) -> bool:
        return bool(settings.OTT_SEARCH_API_URL and settings.OTT_SEARCH_API_KEY)

    def search(self, movie) -> list[dict]:
        if not self.configured: return []
        title = movie.title if hasattr(movie, "title") else str(movie)
        year = getattr(getattr(movie, "release_date", None), "year", "")
        response = httpx.get(settings.OTT_SEARCH_API_URL, params={"q": f"{title} {year} OTT release India"}, headers={"Authorization": f"Bearer {settings.OTT_SEARCH_API_KEY}"}, timeout=15)
        response.raise_for_status(); payload = response.json()
        results = payload.get("results", payload if isinstance(payload, list) else [])
        return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("snippet", ""), "platform": x.get("platform"), "release_date": x.get("release_date")} for x in results if isinstance(x, dict)]


def configured_ott_provider():
    if settings.GOOGLE_SEARCH_API_KEY and settings.GOOGLE_SEARCH_ENGINE_ID:
        return GoogleProgrammableSearchProvider()
    return ConfiguredSearchProvider()
