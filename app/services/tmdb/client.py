"""TMDb API HTTP Client with rate limiting, retries, and error resilience."""

import time
import httpx

from app.config.settings import settings


class TMDbRequestError(RuntimeError):
    """Credential-safe metadata failure with retry/permanence information."""

    def __init__(self, endpoint: str, status_code: int | None = None):
        self.endpoint = endpoint
        self.status_code = status_code
        self.permanent = status_code is not None and 400 <= status_code < 500 and status_code != 429
        suffix = f" with HTTP {status_code}" if status_code is not None else ""
        super().__init__(f"External metadata request failed{suffix} for {endpoint}")


class TMDbClient:
    """Synchronous HTTP client for TMDB v3 REST API."""

    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, request_delay: float = 0.25, max_retries: int = 3):
        headers = {}
        if settings.TMDB_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {settings.TMDB_ACCESS_TOKEN}"
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            timeout=30.0,
            headers=headers,
        )
        self.request_delay = request_delay
        self.max_retries = max_retries

    def get(self, endpoint: str, **params) -> dict:
        """Perform GET request with rate limiting and exponential backoff retries."""
        if settings.TMDB_API_KEY:
            params["api_key"] = settings.TMDB_API_KEY
        elif not settings.TMDB_ACCESS_TOKEN:
            raise RuntimeError("TMDB access is not configured")

        # Enforce minimum inter-request delay
        if self.request_delay > 0:
            time.sleep(self.request_delay)

        attempt = 0
        backoff = 1.0

        last_status = None
        while attempt < self.max_retries:
            attempt += 1
            try:
                response = self.client.get(endpoint, params=params)
                if response.status_code == 429:
                    last_status = 429
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
                    raise TMDbRequestError(endpoint, status) from None
                if attempt >= self.max_retries:
                    print(f"[TMDB_CLIENT] GET {endpoint} failed after {self.max_retries} attempts ({type(e).__name__})")
                    raise TMDbRequestError(endpoint, status) from None
                print(f"[TMDB_CLIENT] GET {endpoint} failed ({type(e).__name__}); retrying in {backoff:.1f}s")
                time.sleep(backoff)
                backoff *= 2.0

        raise TMDbRequestError(endpoint, last_status)
