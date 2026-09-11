import hashlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config.settings import settings
from app.core.rate_limit import _reset_local_fallback_for_tests, limit
from app.core.session_auth import COOKIE
from app.database.connection import get_db
from app.main import app


@pytest.fixture()
def client(database):
    def override_db():
        yield database

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_public_movie_detail_does_not_enqueue_repair(client, monkeypatch):
    def reject_dispatch(*_args, **_kwargs):
        raise AssertionError("public GET attempted to dispatch a Celery task")

    monkeypatch.setattr(
        "app.workers.celery_app.celery_app.send_task", reject_dispatch
    )
    response = client.get("/api/v1/movies/1/detail")
    assert response.status_code == 200
    assert response.json()["repair_queued"] is False


def test_redis_failure_uses_bounded_local_rate_limit(monkeypatch):
    monkeypatch.setattr(
        "app.core.rate_limit.redis.from_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError()),
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/test",
            "headers": [],
            "client": ("192.0.2.10", 1234),
        }
    )
    _reset_local_fallback_for_tests()
    limit(request, "security-test", 1, 60)
    with pytest.raises(HTTPException) as exc:
        limit(request, "security-test", 1, 60)
    assert exc.value.status_code == 429


def test_admin_logout_revokes_copied_session(client, monkeypatch):
    salt = b"security-test-salt"
    password = "correct horse battery staple"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1_000).hex()
    monkeypatch.setattr(
        settings,
        "ADMIN_PASSWORD_HASH",
        f"pbkdf2_sha256$1000${salt.hex()}${digest}",
    )
    login = client.post("/api/v1/admin/login", json={"password": password})
    assert login.status_code == 200
    copied_session = client.cookies.get(COOKIE)
    assert copied_session
    assert client.post("/api/v1/admin/logout").status_code == 200
    client.cookies.set(COOKIE, copied_session)
    assert client.get("/api/v1/admin/session").status_code == 401


def test_admin_responses_are_not_cacheable(client):
    response = client.get("/api/v1/admin/session")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"


def test_global_search_is_rate_limited(client, monkeypatch):
    calls = []

    def record_limit(_request, bucket, maximum, seconds, **_kwargs):
        calls.append((bucket, maximum, seconds))

    monkeypatch.setattr("app.api.v1.public.limit", record_limit)
    response = client.get("/api/v1/search?q=Example")
    assert response.status_code == 200
    assert calls == [("global-search", 120, 60)]
