"""TMDb API HTTP Client with rate limiting, retries, and error resilience."""

import time
import logging
import random
import math
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import httpx

from app.config.settings import settings

log = logging.getLogger(__name__)
_request_context = ContextVar("tmdb_request_context", default={})
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


class _TMDBTransportLogFilter(logging.Filter):
    def filter(self, record):
        # HTTPX's informational request line includes the API key query.
        # Our structured request log above replaces that unsafe duplicate.
        return not any(
            isinstance(arg, httpx.URL) and arg.host == "api.themoviedb.org"
            for arg in (record.args if isinstance(record.args, tuple) else ())
        )


logging.getLogger("httpx").addFilter(_TMDBTransportLogFilter())


@contextmanager
def metadata_request_context(movie_id, tmdb_id):
    token = _request_context.set({"movie_id": movie_id, "tmdb_id": tmdb_id})
    try:
        yield
    finally:
        _request_context.reset(token)


class TMDbRequestError(RuntimeError):
    """Credential-safe metadata failure with retry/permanence information."""

    def __init__(self, endpoint: str, status_code: int | None = None, *, attempts=1):
        endpoint = endpoint.split("?", 1)[0]
        self.endpoint = endpoint
        self.status_code = status_code
        self.attempts = attempts
        self.retryable = status_code is None or status_code in TRANSIENT_STATUSES
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

        context = _request_context.get()
        # Enrichment owns its HTTP retry budget; other clients retain their
        # configured attempt count. Celery must not replay the whole batch.
        maximum = 5 if context else max(1, self.max_retries)
        for attempt in range(1, maximum + 1):
            status = None
            retry_after = 0.0
            try:
                response = self.client.get(endpoint, params=params)
                response.raise_for_status()
                self._log(endpoint, response.status_code, attempt, maximum, 0, "success")
                return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
                retryable = status is None or status in TRANSIENT_STATUSES
                if not retryable or attempt == maximum:
                    self._log(endpoint, status, attempt, maximum, 0,
                              "exhausted" if retryable else "non_transient")
                    raise TMDbRequestError(endpoint, status, attempts=attempt) from None
                if status == 429:
                    retry_after = self._retry_after(e.response.headers.get("Retry-After"))
                # Do not retry earlier than a long server-directed cooldown.
                # Defer instead of occupying a worker indefinitely.
                if retry_after > 120:
                    self._log(endpoint, status, attempt, maximum, retry_after, "deferred")
                    error = TMDbRequestError(endpoint, status, attempts=attempt)
                    error.retry_after = retry_after
                    raise error from None
                base = min(60.0, 5.0 * (2 ** (attempt - 1)))
                delay = max(retry_after, base + random.uniform(0, base * 0.2))
                self._log(endpoint, status, attempt, maximum, delay, "retry")
                time.sleep(delay)

    @staticmethod
    def _retry_after(value):
        if not value:
            return 0.0
        try:
            seconds = float(value)
            return max(0.0, seconds) if math.isfinite(seconds) else 0.0
        except ValueError:
            try:
                when = parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return 0.0

    @staticmethod
    def _log(endpoint, status, attempt, maximum, delay, outcome):
        context = _request_context.get()
        log.info(
            "job=%s movie_id=%s tmdb_id=%s endpoint=%s status=%s "
            "attempt=%s max_attempts=%s next_retry_delay=%.2f outcome=%s",
            "tmdb.metadata_enrichment" if context else "tmdb.request",
            context.get("movie_id"), context.get("tmdb_id"),
            endpoint.split("?", 1)[0], status, attempt, maximum, delay, outcome,
        )
