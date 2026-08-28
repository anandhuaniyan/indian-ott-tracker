"""Redis fixed-window limiter; fails open only when Redis is unavailable."""
import hashlib
from fastapi import HTTPException, Request
import redis
from app.config.settings import settings


def limit(request: Request, bucket: str, maximum: int, seconds: int, *, identity: str | int | None = None):
    try:
        client=redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.2)
        if identity is None:
            subject = request.client.host if request.client else "unknown"
        else:
            # Do not persist private email addresses or other identifiers in
            # Redis keys or operational diagnostics.
            subject = hashlib.sha256(str(identity).strip().lower().encode()).hexdigest()
        key=f"rate:{bucket}:{subject}"
        count=client.incr(key)
        if count == 1: client.expire(key, seconds)
        if count > maximum: raise HTTPException(429,"Too many requests; try again later")
    except HTTPException: raise
    except Exception: pass
