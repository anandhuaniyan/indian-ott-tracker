"""Revocable cookie-based administrator authentication."""

import hashlib
import hmac
import logging
import secrets
import threading
import time

from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import redis

from app.config.settings import settings

COOKIE = "ott_admin_session"
SESSION_TTL_SECONDS = 60 * 60 * 8
logger = logging.getLogger(__name__)
_local_lock = threading.Lock()
_local_sessions: dict[str, tuple[float, bool]] = {}


def _signer():
    return URLSafeTimedSerializer(
        settings.ADMIN_SESSION_SECRET or settings.SECRET_KEY,
        salt="admin-session",
    )


def _credential_version() -> str:
    return hashlib.sha256(settings.ADMIN_PASSWORD_HASH.encode()).hexdigest()


def _session_key(session_id: str) -> str:
    return f"admin_session:{hashlib.sha256(session_id.encode()).hexdigest()}"


def _redis_client():
    return redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )


def _remember_local(session_id: str, persisted: bool) -> None:
    now = time.monotonic()
    with _local_lock:
        expired = [sid for sid, value in _local_sessions.items() if value[0] <= now]
        for sid in expired:
            _local_sessions.pop(sid, None)
        if len(_local_sessions) >= 1_000:
            _local_sessions.pop(next(iter(_local_sessions)))
        _local_sessions[session_id] = (now + SESSION_TTL_SECONDS, persisted)


def _local_session(session_id: str) -> tuple[bool, bool]:
    now = time.monotonic()
    with _local_lock:
        value = _local_sessions.get(session_id)
        if value is None or value[0] <= now:
            _local_sessions.pop(session_id, None)
            return False, False
        return True, value[1]


def verify_password(value: str) -> bool:
    # ADMIN_PASSWORD_HASH format: pbkdf2_sha256$iterations$salt_hex$digest_hex
    try:
        scheme, rounds, salt, digest = settings.ADMIN_PASSWORD_HASH.split("$")
        if scheme != "pbkdf2_sha256": return False
        actual = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, digest)
    except ValueError: return False


def create_session() -> str:
    session_id = secrets.token_urlsafe(32)
    persisted = False
    try:
        _redis_client().setex(_session_key(session_id), SESSION_TTL_SECONDS, "1")
        persisted = True
    except Exception as exc:
        logger.warning(
            "Redis admin session store unavailable; using local revocable session (%s)",
            type(exc).__name__,
        )
    _remember_local(session_id, persisted)
    return _signer().dumps(
        {"admin": True, "sid": session_id, "cv": _credential_version()}
    )


def require_admin_session(ott_admin_session: str | None = Cookie(default=None)):
    if not ott_admin_session: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    try:
        payload = _signer().loads(ott_admin_session, max_age=SESSION_TTL_SECONDS)
        session_id = payload.get("sid")
        if (
            not payload.get("admin")
            or not isinstance(session_id, str)
            or not hmac.compare_digest(payload.get("cv", ""), _credential_version())
        ):
            raise ValueError
        local_valid, persisted = _local_session(session_id)
        if persisted:
            try:
                if not _redis_client().exists(_session_key(session_id)):
                    raise ValueError
            except ValueError:
                raise
            except Exception:
                if not local_valid:
                    raise ValueError
        elif not local_valid:
            raise ValueError
        return session_id
    except (BadSignature, SignatureExpired, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")


def revoke_session(token: str | None) -> None:
    if not token:
        return
    try:
        payload = _signer().loads(token, max_age=SESSION_TTL_SECONDS)
        session_id = payload.get("sid")
        if not isinstance(session_id, str):
            return
    except (BadSignature, SignatureExpired):
        return
    local_valid, persisted = _local_session(session_id)
    if persisted:
        try:
            _redis_client().delete(_session_key(session_id))
        except Exception as exc:
            logger.warning("Admin logout revocation failed (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Logout could not be completed; please retry",
            )
    if local_valid:
        with _local_lock:
            _local_sessions.pop(session_id, None)


def _reset_session_store_for_tests() -> None:
    with _local_lock:
        _local_sessions.clear()


def require_same_origin(request: Request):
    """Reject cross-origin cookie-authenticated mutations while allowing non-browser clients."""
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {item.strip().rstrip("/") for item in settings.FRONTEND_ORIGINS.split(",") if item.strip()}
    allowed.add(settings.SITE_URL.rstrip("/"))
    if origin.rstrip("/") not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-origin admin request rejected")
