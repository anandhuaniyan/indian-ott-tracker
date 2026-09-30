"""Normal website-user authentication, separate from administrator sessions."""

import hashlib
import logging
import secrets
import threading
import time

import redis
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.connection import get_db
from app.models.community_rating import User

USER_COOKIE = "ott_user_session"
USER_SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
_hasher = PasswordHasher()
_logger = logging.getLogger(__name__)
_local_lock = threading.Lock()
_local_sessions: dict[str, tuple[float, int, int, bool]] = {}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_user_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="user-session")


def _session_key(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    return f"user_session:{digest}"


def _redis_client():
    return redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )


def create_user_session(user_id: int, session_version: int = 0) -> str:
    session_id = secrets.token_urlsafe(32)
    persisted = False
    try:
        _redis_client().setex(
            _session_key(session_id),
            USER_SESSION_TTL_SECONDS,
            f"{user_id}:{session_version}",
        )
        persisted = True
    except Exception as exc:
        _logger.warning(
            "Redis user session store unavailable; using local revocable session (%s)",
            type(exc).__name__,
        )
    with _local_lock:
        _local_sessions[session_id] = (
            time.monotonic() + USER_SESSION_TTL_SECONDS,
            user_id,
            session_version,
            persisted,
        )
    return _signer().dumps(
        {"uid": user_id, "sid": session_id, "ver": session_version}
    )


def _session_identity(token: str | None) -> tuple[int, int] | None:
    if not token:
        return None
    try:
        payload = _signer().loads(token, max_age=USER_SESSION_TTL_SECONDS)
        session_id = payload.get("sid")
        user_id = payload.get("uid")
        session_version = payload.get("ver", 0)
        if (
            not isinstance(session_id, str)
            or not isinstance(user_id, int)
            or not isinstance(session_version, int)
        ):
            return None
    except (BadSignature, SignatureExpired):
        return None

    now = time.monotonic()
    with _local_lock:
        local = _local_sessions.get(session_id)
        if local and local[0] <= now:
            _local_sessions.pop(session_id, None)
            local = None
    if local and local[1] == user_id and local[2] == session_version:
        if not local[3]:
            return user_id, session_version
        try:
            stored = _redis_client().get(_session_key(session_id))
            if stored is not None and stored.decode() == f"{user_id}:{session_version}":
                return user_id, session_version
            return None
        except Exception:
            return user_id, session_version
    try:
        stored = _redis_client().get(_session_key(session_id))
        return (
            (user_id, session_version)
            if stored is not None and stored.decode() == f"{user_id}:{session_version}"
            else None
        )
    except Exception:
        return None


def _session_user_id(token: str | None) -> int | None:
    identity = _session_identity(token)
    return identity[0] if identity else None


def require_user(
    token: str | None = Cookie(default=None, alias=USER_COOKIE),
    db: Session = Depends(get_db),
) -> User:
    identity = _session_identity(token)
    user_id = identity[0] if identity else None
    user = db.get(User, user_id) if user_id is not None else None
    if (
        user is None
        or not user.is_active
        or identity is None
        or identity[1] != user.auth_session_version
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User login required",
        )
    return user


def revoke_user_session(token: str | None) -> None:
    if not token:
        return
    try:
        payload = _signer().loads(token, max_age=USER_SESSION_TTL_SECONDS)
        session_id = payload.get("sid")
        if not isinstance(session_id, str):
            return
    except (BadSignature, SignatureExpired):
        return
    with _local_lock:
        local = _local_sessions.pop(session_id, None)
    if local and local[3]:
        try:
            _redis_client().delete(_session_key(session_id))
        except Exception as exc:
            _logger.warning("User logout revocation failed (%s)", type(exc).__name__)
            raise HTTPException(503, "Logout could not be completed; please retry")


def require_user_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {
        item.strip().rstrip("/")
        for item in settings.FRONTEND_ORIGINS.split(",")
        if item.strip()
    }
    allowed.add(settings.SITE_URL.rstrip("/"))
    if origin.rstrip("/") not in allowed:
        raise HTTPException(403, "Cross-origin user request rejected")


def _reset_user_session_store_for_tests() -> None:
    with _local_lock:
        _local_sessions.clear()
