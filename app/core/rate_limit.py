"""Redis fixed-window limiter; fails open only when Redis is unavailable."""
from fastapi import HTTPException, Request
import redis
from app.config.settings import settings
def limit(request: Request, bucket: str, maximum: int, seconds: int):
    try:
        client=redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.2)
        key=f"rate:{bucket}:{request.client.host if request.client else 'unknown'}"
        count=client.incr(key)
        if count == 1: client.expire(key, seconds)
        if count > maximum: raise HTTPException(429,"Too many requests; try again later")
    except HTTPException: raise
    except Exception: pass
