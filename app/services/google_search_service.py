"""Deprecated compatibility shim.

Consumer-search scraping is deliberately disabled. Use the configured lawful
OTT research provider in ``app.services.ott_providers`` instead.
"""

from datetime import date, datetime, timezone
import re
import urllib.parse
import httpx

from app.models.movie import Movie


KNOWN_OTT_PLATFORMS = {
    "netflix": "Netflix",
    "prime video": "Amazon Prime Video",
    "amazon prime": "Amazon Prime Video",
    "amazon prime video": "Amazon Prime Video",
    "jiohotstar": "JioHotstar",
    "hotstar": "JioHotstar",
    "disney+ hotstar": "JioHotstar",
    "disney hotstar": "JioHotstar",
    "jiocinema": "JioCinema",
    "sony liv": "Sony LIV",
    "sonyliv": "Sony LIV",
    "zee5": "ZEE5",
    "sun nxt": "Sun NXT",
    "sunnxt": "Sun NXT",
    "aha": "Aha",
    "manoramamax": "ManoramaMAX",
    "mx player": "MX Player",
    "apple tv": "Apple TV+",
    "apple tv+": "Apple TV+",
    "youtube": "YouTube",
}

HIGH_AUTHORITY_DOMAINS = [
    "netflix.com",
    "primevideo.com",
    "hotstar.com",
    "jiocinema.com",
    "sonyliv.com",
    "zee5.com",
    "sunnxt.com",
    "aha.video",
    "manoramamax.com",
    "themoviedb.org",
    "justwatch.com",
]


class GoogleSearchOttService:
    """Fallback service that searches web queries to infer OTT availability and release dates.
    Enforces strict confidence scoring and caching of search queries.
    """

    def __init__(self):
        self._cache: dict[str, dict] = {}

    def search_ott_release(self, movie: Movie) -> dict | None:
        """Perform fallback search for a movie and return parsed metadata if confidence >= 90.0%."""
        title = movie.title
        queries = [
            f'"{title}" OTT Release India',
            f'"{title}" Netflix Prime Hotstar streaming',
        ]

        best_result = None
        highest_confidence = 0.0

        for query in queries:
            result = self._execute_search_cached(query, movie)
            if not result:
                continue

            confidence = result.get("confidence", 0.0)
            if confidence > highest_confidence:
                highest_confidence = confidence
                best_result = result

            if highest_confidence >= 90.0:
                break

        if best_result and highest_confidence >= 90.0:
            print(f"[GOOGLE_SEARCH_FALLBACK] High confidence ({highest_confidence:.1f}%) match for '{title}': {best_result['provider']} on {best_result.get('ott_release_date')}")
            return best_result
        elif best_result:
            print(f"[GOOGLE_SEARCH_FALLBACK] Logged low confidence ({highest_confidence:.1f}%) result for '{title}' (Ignored, threshold=90%)")
            return None
        else:
            return None

    def _execute_search_cached(self, query: str, movie: Movie) -> dict | None:
        if query in self._cache:
            return self._cache[query]

        result = self._perform_search_and_parse(query, movie)
        self._cache[query] = result
        return result

    def _perform_search_and_parse(self, query: str, movie: Movie) -> dict | None:
        """Disabled: deployments must opt into a lawful provider explicitly."""
        return None

        # Historical parser retained below for migration reference only.
        text = ""

        # Parse text snippets for OTT platforms, dates, and domain matches
        found_platform = None
        found_url = None
        matched_domain = False

        text_lower = text.lower()
        for kw, platform_name in KNOWN_OTT_PLATFORMS.items():
            if kw in text_lower:
                found_platform = platform_name
                break

        for domain in HIGH_AUTHORITY_DOMAINS:
            if domain in text_lower:
                matched_domain = True
                found_url = f"https://www.{domain}"
                break

        if not found_platform:
            return None

        # Calculate Confidence Score (0.0 - 100.0)
        confidence = 0.0

        # Title match check (+40%)
        if movie.title.lower() in text_lower:
            confidence += 40.0

        # Known platform match (+30%)
        if found_platform:
            confidence += 30.0

        # High authority domain match (+20%)
        if matched_domain:
            confidence += 20.0

        # Release date pattern match (+10%)
        parsed_date = self._extract_date(text)
        if parsed_date:
            confidence += 10.0

        return {
            "provider": found_platform,
            "provider_logo": None,
            "country": "IN",
            "watch_type": "subscription",
            "ott_release_date": parsed_date,
            "status": "available" if parsed_date and parsed_date <= date.today() else "announced",
            "source_type": "GOOGLE_SEARCH",
            "source_url": found_url or search_url,
            "confidence": min(confidence, 100.0),
            "last_checked": datetime.now(timezone.utc),
        }

    def _extract_date(self, text: str) -> date | None:
        """Extract YYYY-MM-DD or standard Indian date patterns from text."""
        # Match YYYY-MM-DD
        m1 = re.search(r"\b(202[4-9]-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))\b", text)
        if m1:
            try:
                return date.fromisoformat(m1.group(1))
            except ValueError:
                pass

        # Match DD Month YYYY (e.g. 15 August 2026)
        months = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
        m2 = re.search(rf"\b(\d{{1,2}})\s+{months}\s+(202[4-9])\b", text, re.IGNORECASE)
        if m2:
            try:
                month_str = m2.group(2)[:3].title()
                month_num = datetime.strptime(month_str, "%b").month
                return date(int(m2.group(3)), month_num, int(m2.group(1)))
            except Exception:
                pass

        return None
