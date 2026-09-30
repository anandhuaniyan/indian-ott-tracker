"""Short-lived, single-use Pinterest OAuth state bound to an admin session."""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time

import redis

from app.config.settings import settings


STATE_TTL_SECONDS = 600
logger = logging.getLogger(__name__)
_lock = threading.Lock()
_local_states: dict[str, tuple[str, float, bool]] = {}


def _key(state: str) -> str:
    return f"pinterest_oauth_state:{hashlib.sha256(state.encode()).hexdigest()}"


def _client():
    return redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )


def create_state(admin_session_id: str) -> str:
    state = secrets.token_urlsafe(32)
    persisted = False
    try:
        _client().setex(_key(state), STATE_TTL_SECONDS, admin_session_id)
        persisted = True
    except Exception as exc:
        logger.warning(
            "Redis Pinterest OAuth state store unavailable; using local state (%s)",
            type(exc).__name__,
        )
    now = time.monotonic()
    with _lock:
        expired = [key for key, value in _local_states.items() if value[1] <= now]
        for key in expired:
            _local_states.pop(key, None)
        _local_states[state] = (
            admin_session_id,
            now + STATE_TTL_SECONDS,
            persisted,
        )
    return state


def consume_state(state: str) -> str | None:
    now = time.monotonic()
    with _lock:
        local = _local_states.pop(state, None)
    if local is None or local[1] <= now:
        return None
    admin_session_id, _, persisted = local
    if persisted:
        try:
            stored = _client().getdel(_key(state))
        except Exception as exc:
            logger.warning(
                "Redis Pinterest OAuth state validation unavailable; using local state (%s)",
                type(exc).__name__,
            )
        else:
            if stored is None:
                return None
            stored_session = stored.decode() if isinstance(stored, bytes) else str(stored)
            if not secrets.compare_digest(stored_session, admin_session_id):
                return None
    return admin_session_id


def _reset_oauth_states_for_tests() -> None:
    with _lock:
        _local_states.clear()
