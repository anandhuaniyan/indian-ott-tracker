"""Public-safe error formatting for provider and background-job failures."""

from __future__ import annotations

import re

from app.config.settings import settings


_QUERY_SECRET = re.compile(
    r"(?i)((?:[?&]|\b)(?:api_key|apikey|key|token|access_token)=)[^&#,\s]+"
)
_AUTH_SECRET = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^,;\s]+")


def sanitize_error(value: object, *, limit: int = 2000) -> str:
    """Return a bounded diagnostic with configured credentials removed.

    Provider exception strings can contain a fully rendered request URL.  This
    helper is intentionally used at persistence/logging boundaries so raw
    exceptions never become admin data, Celery results, or notification text.
    """

    message = str(value or "External service error")
    known_secrets = (
        settings.TMDB_API_KEY,
        settings.TMDB_ACCESS_TOKEN,
        settings.IMDB_RATING_API_KEY,
        settings.OTT_SEARCH_API_KEY,
        settings.GOOGLE_SEARCH_API_KEY,
        settings.TAVILY_API_KEY,
        settings.OTTPLAY_API_KEY,
        settings.JUSTWATCH_API_KEY,
        settings.SMTP_PASSWORD,
        settings.SMTP_USERNAME,
        settings.TELEGRAM_BOT_TOKEN,
        settings.DISCORD_WEBHOOK_URL,
    )
    for secret in known_secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    message = _QUERY_SECRET.sub(r"\1[redacted]", message)
    message = _AUTH_SECRET.sub(r"\1[redacted]", message)
    return message[:limit]
