"""Redis fixed-window limiter with a bounded local outage fallback."""

from __future__ import annotations

import hashlib
import logging
import threading
import time

from fastapi import HTTPException, Request
import redis

from app.config.settings import settings


logger = logging.getLogger(__name__)
_fallback_lock = threading.Lock()
_fallback_windows: dict[str, tuple[int, float]] = {}
_fallback_max_keys = 10_000
_last_redis_warning_at = 0.0


def _subject(request: Request, identity: str | int | None) -> str:
    if identity is None:
        return request.client.host if request.client else "unknown"
    # Do not persist private email addresses or identifiers in rate-limit keys.
    return hashlib.sha256(str(identity).strip().lower().encode()).hexdigest()


def _local_fallback_limit(key: str, maximum: int, seconds: int) -> None:
    """Apply the same fixed-window policy in-process while Redis is unavailable."""
    now = time.monotonic()
    with _fallback_lock:
        current = _fallback_windows.get(key)
        if current is None or current[1] <= now:
            if len(_fallback_windows) >= _fallback_max_keys:
                expired = [name for name, value in _fallback_windows.items() if value[1] <= now]
                for name in expired:
                    _fallback_windows.pop(name, None)
                if len(_fallback_windows) >= _fallback_max_keys:
                    _fallback_windows.pop(next(iter(_fallback_windows)))
            count, expires_at = 1, now + seconds
        else:
            count, expires_at = current[0] + 1, current[1]
        _fallback_windows[key] = (count, expires_at)
    if count > maximum:
        raise HTTPException(429, "Too many requests; try again later")


def _warn_redis_unavailable(exc: Exception) -> None:
    global _last_redis_warning_at
    now = time.monotonic()
    with _fallback_lock:
        should_log = now - _last_redis_warning_at >= 60
        if should_log:
            _last_redis_warning_at = now
    if should_log:
        logger.warning(
            "Redis rate limiter unavailable; using bounded local fallback (%s)",
            type(exc).__name__,
        )


def _reset_local_fallback_for_tests() -> None:
    """Reset process-local limiter state between isolated tests."""
    global _last_redis_warning_at
    with _fallback_lock:
        _fallback_windows.clear()
        _last_redis_warning_at = 0.0


def limit(
    request: Request,
    bucket: str,
    maximum: int,
    seconds: int,
    *,
    identity: str | int | None = None,
) -> None:
    key = f"rate:{bucket}:{_subject(request, identity)}"
    try:
        client = redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        count = client.incr(key)
        if count == 1:
            client.expire(key, seconds)
        if count > maximum:
            raise HTTPException(429, "Too many requests; try again later")
    except HTTPException:
        raise
    except Exception as exc:
        _warn_redis_unavailable(exc)
        _local_fallback_limit(key, maximum, seconds)
