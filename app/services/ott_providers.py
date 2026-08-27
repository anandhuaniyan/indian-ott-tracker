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

    def search(self, movie, *, max_queries: int | None = None, before_query=None) -> list[dict]:
        if not self.configured: return []
        title = movie.title if hasattr(movie, "title") else str(movie)
        year = getattr(getattr(movie, "release_date", None), "year", "")
        language = getattr(movie, "original_language", "") or ""
        identity = " ".join(str(x) for x in (f'"{title}"', year, language) if x)
        queries = (f"{identity} OTT release India", f"{identity} streaming Netflix Prime Video JioHotstar SonyLIV ZEE5", f"{identity} digital release date official")
        results: list[dict] = []; seen: set[str] = set()
        self.last_query_count = 0
        for query in queries[: max_queries or len(queries)]:
            if before_query and not before_query():
                break
            self.last_query_count += 1
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

    def search(self, movie, *, max_queries: int | None = None, before_query=None) -> list[dict]:
        if not self.configured: return []
        if before_query and not before_query(): return []
        self.last_query_count = 1
        title = movie.title if hasattr(movie, "title") else str(movie)
        year = getattr(getattr(movie, "release_date", None), "year", "")
        response = httpx.get(settings.OTT_SEARCH_API_URL, params={"q": f"{title} {year} OTT release India"}, headers={"Authorization": f"Bearer {settings.OTT_SEARCH_API_KEY}"}, timeout=15)
        response.raise_for_status(); payload = response.json()
        results = payload.get("results", payload if isinstance(payload, list) else [])
        return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("snippet", ""), "platform": x.get("platform"), "release_date": x.get("release_date")} for x in results if isinstance(x, dict)]


class TavilySearchProvider:
    """Free-budgeted Tavily adapter dedicated to platform/date evidence."""

    endpoint = "https://api.tavily.com/search"
    is_tavily = True

    def __init__(self, api_key: str | None = None, endpoint: str | None = None, timeout: float = 20):
        self.api_key = (settings.TAVILY_API_KEY or settings.OTT_SEARCH_API_KEY) if api_key is None else api_key
        self.endpoint = endpoint or settings.OTT_SEARCH_API_URL or self.endpoint
        self.timeout = timeout
        self.last_query_count = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _matches_movie(movie, text: str) -> bool:
        normalized_title = re.sub(r"[^a-z0-9]+", " ", movie.title.lower()).strip()
        normalized_text = re.sub(r"[^a-z0-9]+", " ", text.lower())
        if normalized_title and normalized_title not in normalized_text:
            return False
        release_date = getattr(movie, "theatrical_release_date", None) or getattr(movie, "release_date", None)
        expected_year = getattr(release_date, "year", None)
        mentioned_years = {int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", text)}
        return not (expected_year and mentioned_years and all(abs(year - expected_year) > 1 for year in mentioned_years))

    def _request(self, query: str, before_query=None) -> list[dict]:
        if before_query and not before_query():
            return []
        self.last_query_count += 1
        response = httpx.post(
            self.endpoint,
            json={
                "api_key": self.api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 8,
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=self.timeout,
            follow_redirects=True,
        )
        if response.status_code in {402, 429}:
            raise RuntimeError("Tavily free quota or rate limit reached")
        response.raise_for_status()
        return response.json().get("results", [])

    def search(self, movie, *, max_queries: int | None = None, before_query=None) -> list[dict]:
        if not self.configured:
            return []
        self.last_query_count = 0
        maximum = max(1, min(max_queries or settings.TAVILY_MAX_QUERIES_PER_MOVIE, settings.TAVILY_MAX_QUERIES_PER_MOVIE))
        release_date = getattr(movie, "theatrical_release_date", None) or getattr(movie, "release_date", None)
        year = getattr(release_date, "year", "")
        language = getattr(movie, "original_language", "") or ""
        identity = " ".join(str(value) for value in (f'"{movie.title}"', year, language) if value)
        queries = [
            f"{identity} OTT release date streaming platform India",
            f"{identity} digital streaming release official Netflix Prime Video JioHotstar SonyLIV ZEE5",
        ]
        found: list[dict] = []
        seen: set[str] = set()
        for index, query in enumerate(queries[:maximum]):
            raw_results = self._request(query, before_query)
            for item in raw_results:
                url = item.get("url")
                text = " ".join(filter(None, (item.get("title"), item.get("content"))))
                if not url or url in seen or not self._matches_movie(movie, text):
                    continue
                seen.add(url)
                found.append({
                    "title": item.get("title"),
                    "url": url,
                    "snippet": item.get("content", ""),
                    "platform": _platform(url, text),
                    "release_date": _release_date(text),
                    "published_date": item.get("published_date"),
                    "query": query,
                })
            # Spend a second credit only when the first query did not identify
            # both pieces of information Tavily is allowed to research.
            if index == 0 and any(item.get("platform") and item.get("release_date") for item in found):
                break
        return found


def configured_ott_provider():
    provider = (settings.OTT_RESEARCH_PROVIDER or "").strip().lower()
    if provider == "tavily" or settings.TAVILY_API_KEY or "tavily.com" in settings.OTT_SEARCH_API_URL.lower():
        return TavilySearchProvider()
    if provider in {"google", "google_programmable_search"} and settings.GOOGLE_SEARCH_API_KEY and settings.GOOGLE_SEARCH_ENGINE_ID:
        return GoogleProgrammableSearchProvider()
    return ConfiguredSearchProvider()
