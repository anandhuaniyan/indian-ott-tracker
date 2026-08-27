"""TMDb API HTTP Client with rate limiting, retries, and error resilience."""

import time
import httpx

from app.config.settings import settings


class TMDbClient:
    """Synchronous HTTP client for TMDB v3 REST API."""

    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, request_delay: float = 0.25, max_retries: int = 3):
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            timeout=30.0,
        )
        self.request_delay = request_delay
        self.max_retries = max_retries

    def get(self, endpoint: str, **params) -> dict:
        """Perform GET request with rate limiting and exponential backoff retries."""
        params["api_key"] = settings.TMDB_API_KEY

        # Enforce minimum inter-request delay
        if self.request_delay > 0:
            time.sleep(self.request_delay)

        attempt = 0
        backoff = 1.0

        while attempt < self.max_retries:
            attempt += 1
            try:
                response = self.client.get(endpoint, params=params)
                if response.status_code == 429:
                    print(f"[TMDB_CLIENT] Rate limited (429). Retrying in {backoff:.1f}s (Attempt {attempt}/{self.max_retries})...")
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue

                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
                if status is not None and 400 <= status < 500 and status != 429:
                    print(f"[TMDB_CLIENT] GET {endpoint} rejected with HTTP {status}; not retrying")
                    raise
                if attempt >= self.max_retries:
                    print(f"[TMDB_CLIENT] GET {endpoint} failed after {self.max_retries} attempts ({type(e).__name__})")
                    raise e
                print(f"[TMDB_CLIENT] GET {endpoint} failed ({type(e).__name__}); retrying in {backoff:.1f}s")
                time.sleep(backoff)
                backoff *= 2.0

        raise RuntimeError(f"Failed GET {endpoint} after {self.max_retries} retries")
