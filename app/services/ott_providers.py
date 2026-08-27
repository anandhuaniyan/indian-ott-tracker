"""Lawful, opt-in OTT research provider adapter and source scoring."""
from datetime import date
import httpx
from app.config.settings import settings

OFFICIAL = ("netflix.com", "primevideo.com", "hotstar.com", "jiohotstar.com", "jiocinema.com", "sonyliv.com", "zee5.com", "aha.video", "sunnxt.com", "manoramamax.com", "mxplayer.in")
OFFICIAL_ANNOUNCEMENT = ("youtube.com", "instagram.com", "x.com", "facebook.com")
TRUSTED = ("variety.com", "hollywoodreporter.com", "deadline.com", "screendaily.com", "indiatoday.in", "thehindu.com", "indianexpress.com", "filmcompanion.in", "cinemaexpress.com")
def source_rank(url: str | None) -> tuple[str, float]:
    domain = (url or "").lower()
    if any(x in domain for x in OFFICIAL): return "official_platform", 95.0
    if any(x in domain for x in OFFICIAL_ANNOUNCEMENT): return "official_announcement_needs_identity_verification", 80.0
    if any(x in domain for x in TRUSTED): return "established_publication", 82.0
    return "unknown", 35.0

class ConfiguredSearchProvider:
    """Adapter for a consented search API; never calls a consumer search website."""
    def search(self, title: str) -> list[dict]:
        if not settings.OTT_SEARCH_API_URL or not settings.OTT_SEARCH_API_KEY: return []
        response = httpx.get(settings.OTT_SEARCH_API_URL, params={"q": f"{title} OTT release India"}, headers={"Authorization": f"Bearer {settings.OTT_SEARCH_API_KEY}"}, timeout=15)
        response.raise_for_status(); payload = response.json()
        results = payload.get("results", payload if isinstance(payload, list) else [])
        return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("snippet", ""), "platform": x.get("platform"), "release_date": x.get("release_date")} for x in results if isinstance(x, dict)]
