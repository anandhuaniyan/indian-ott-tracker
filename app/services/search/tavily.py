"""Tavily search client for OTT evidence discovery."""

import logging
from tavily import TavilyClient
from app.config.settings import settings

logger = logging.getLogger(__name__)

class TavilySearchClient:
    def __init__(self):
        self.api_key = settings.TAVILY_API_KEY
        self.client = TavilyClient(api_key=self.api_key) if self.api_key else None

    def search(self, query: str, max_results: int = 5, search_depth: str = "advanced") -> dict:
        if not self.client:
            logger.error("Tavily API key not configured")
            return {"results": []}

        try:
            return self.client.search(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
                include_answer=False
            )
        except Exception as e:
            error_msg = str(e)
            if "usage limit" in error_msg or "432" in error_msg:
                logger.error("Tavily quota exceeded")
                return {"error": "QUOTA_EXCEEDED"}
            logger.error(f"Tavily search error: {e}")
            return {"results": []}
