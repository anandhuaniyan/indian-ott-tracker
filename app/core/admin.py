"""Small, deployment-friendly guard for write-only administration endpoints."""
from fastapi import Header, HTTPException, status
from app.config.settings import settings

def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    if not settings.ADMIN_API_KEY or x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin authentication required")
