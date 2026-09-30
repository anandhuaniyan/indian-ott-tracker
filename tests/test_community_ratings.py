"""Normal-user authentication and community rating integration coverage."""

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config.settings import settings
from app.database.connection import get_db
from app.main import app
from app.models.community_rating import CommunityRating, User


@pytest.fixture()
def client(database):
    def override():
        yield database

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _register(client, email, password="StrongPass123", display_name=None):
    payload = {"email": email, "password": password}
    if display_name:
        payload["display_name"] = display_name
    response = client.post("/api/v1/auth/register", json=payload)
    # Tests outside the dedicated verification suite need an authenticated user.
    # Production users reach this state only through the single-use email token.
    if response.status_code == 201:
        override = app.dependency_overrides[get_db]()
        database = next(override)
        user = database.query(User).filter_by(email=email.strip().casefold()).one()
        user.email_verified_at = datetime.now(timezone.utc)
        database.commit()
        login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200
    return response


def _admin_login(client, monkeypatch):
    salt = b"0123456789abcdef"
    password = "admin-password"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(
        settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}"
    )
    return client.post("/api/v1/admin/login", json={"password": password})


def test_user_register_current_user_password_hash_and_duplicate_email(client, database):
    response = _register(client, "User@Example.com", display_name="User A")
    assert response.status_code == 201
    assert "set-cookie" not in response.headers
    assert response.json()["email"] == "user@example.com"
    assert {"id", "email", "display_name", "role", "email_verified", "support_eligible", "verification_required", "email_status"} == set(response.json())
    assert response.json()["role"] == "USER"
    user = database.query(User).one()
    assert user.password_hash != "StrongPass123"
    assert user.password_hash.startswith("$argon2id$")
    assert client.get("/api/v1/auth/me").json()["email"] == response.json()["email"]
    assert "password_hash" not in client.get("/api/v1/auth/me").json()
    assert _register(client, "USER@example.com").status_code == 409
    assert database.query(User).count() == 1


@pytest.mark.parametrize("payload", [
    {"email": "not-an-email", "password": "StrongPass123"},
    {"email": "user@example.com", "password": "short"},
    {"email": "user@example.com", "password": "allletterslong"},
])
def test_registration_validation_rejects_invalid_credentials(client, payload):
    assert client.post("/api/v1/auth/register", json=payload).status_code == 422


def test_user_login_invalid_login_logout_and_admin_separation(client, monkeypatch):
    assert _register(client, "viewer@example.com").status_code == 201
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/login", json={
        "email": "viewer@example.com", "password": "wrong-password",
    }).status_code == 401
    assert client.post("/api/v1/auth/login", json={
        "email": "viewer@example.com", "password": "StrongPass123",
    }).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.get("/api/v1/admin/dashboard").status_code == 403
    assert _admin_login(client, monkeypatch).status_code == 200
    assert client.get("/api/v1/admin/dashboard").status_code == 200


def test_login_failure_limits_are_separate_and_success_does_not_consume_them(client):
    assert _register(client, "target@example.com").status_code == 201
    assert client.post("/api/v1/auth/logout").status_code == 200

    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={
            "email": "target@example.com", "password": "wrong-password",
        }).status_code == 401
    blocked = client.post("/api/v1/auth/login", json={
        "email": "target@example.com", "password": "wrong-password",
    })
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert "Please try again" in blocked.json()["detail"]

    # The account-specific bucket must not lock out a different normal user on
    # the same client IP, and successful logins do not increment failure state.
    assert _register(client, "other@example.com").status_code == 201
    assert client.post("/api/v1/auth/logout").status_code == 200
    for _ in range(3):
        assert client.post("/api/v1/auth/login", json={
            "email": "other@example.com", "password": "StrongPass123",
        }).status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 200


def test_admin_login_does_not_authenticate_normal_user(client, monkeypatch):
    assert _admin_login(client, monkeypatch).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.put("/api/v1/movies/1/community-rating", json={"rating": 8}).status_code == 401


def test_admin_uses_normal_login_and_role_is_server_authoritative(client, database, monkeypatch):
    salt = b"0123456789abcdef"
    password = "admin-password"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "owner@example.com")
    monkeypatch.setattr(
        settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}"
    )

    login = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": password,
    })
    assert login.status_code == 200
    assert login.json()["role"] == "ADMIN"
    assert database.query(User).filter_by(email="owner@example.com").one().role == "ADMIN"
    assert client.get("/api/v1/admin/dashboard").status_code == 200

    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/admin/dashboard").status_code == 401


def test_public_rating_read_and_backend_validation(client, database):
    assert client.get("/api/v1/movies/1/community-rating").json() == {"average": None, "count": 0}
    assert client.get("/api/v1/movies/1/detail").json()["community_rating"] == {"average": None, "count": 0}
    assert client.put("/api/v1/movies/1/community-rating", json={"rating": 8.5}).status_code == 401
    assert _register(client, "rater@example.com").status_code == 201
    for invalid in (0.5, 10.5, 7.2):
        assert client.put("/api/v1/movies/1/community-rating", json={"rating": invalid}).status_code == 422
    assert database.query(CommunityRating).count() == 0


def test_two_users_rate_same_movie_update_ownership_average_and_count(client, database):
    assert _register(client, "a@example.com", display_name="User A").status_code == 201
    user_a = database.query(User).filter_by(email="a@example.com").one()
    assert client.put("/api/v1/movies/1/community-rating", json={"rating": 8}).json()["created"] is True
    with TestClient(app) as client_b:
        assert _register(client_b, "b@example.com", display_name="User B").status_code == 201
        user_b = database.query(User).filter_by(email="b@example.com").one()
        second = client_b.put("/api/v1/movies/1/community-rating", json={"rating": 10})
        assert second.status_code == 200
        assert second.json()["average"] == 9
        assert second.json()["count"] == 2
        assert client_b.put("/api/v1/movies/1/community-rating", json={"rating": 9}).json()["created"] is False
        assert float(database.query(CommunityRating).filter_by(user_id=user_a.id, movie_id=1).one().rating) == 8

    updated = client.put("/api/v1/movies/1/community-rating", json={"rating": 6})
    assert updated.status_code == 200
    assert updated.json()["created"] is False
    assert updated.json()["average"] == 7.5
    assert updated.json()["count"] == 2
    assert database.query(CommunityRating).filter_by(movie_id=1).count() == 2
    assert database.query(CommunityRating).filter_by(user_id=user_a.id, movie_id=1).count() == 1
    assert database.query(CommunityRating).filter_by(user_id=user_b.id, movie_id=1).count() == 1


def test_exact_required_two_user_average_scenario(client, database):
    assert _register(client, "first@example.com").status_code == 201
    assert client.put("/api/v1/movies/1/community-rating", json={"rating": 8}).status_code == 200
    with TestClient(app) as second:
        assert _register(second, "second@example.com").status_code == 201
        result = second.put("/api/v1/movies/1/community-rating", json={"rating": 10}).json()
        assert result["average"] == 9 and result["count"] == 2
    result = client.put("/api/v1/movies/1/community-rating", json={"rating": 6}).json()
    assert result["average"] == 8 and result["count"] == 2
    assert database.query(CommunityRating).count() == 2


def test_database_rejects_duplicate_user_movie(database):
    user = User(email="unique@example.com", password_hash="$argon2id$test-only", is_active=True)
    database.add(user)
    database.flush()
    database.add_all([
        CommunityRating(user_id=user.id, movie_id=1, rating=6),
        CommunityRating(user_id=user.id, movie_id=1, rating=8),
    ])
    with pytest.raises(IntegrityError):
        database.commit()
    database.rollback()
