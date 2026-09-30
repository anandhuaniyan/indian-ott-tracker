"""Email-verification, password-reset, and private Support security coverage."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core import user_auth
from app.core.user_auth import hash_password, verify_user_password
from app.database.connection import get_db
from app.main import app
from app.models.community_rating import User, UserAuthToken
from app.models.support import SupportAuditLog, SupportContribution
from app.services.account_tokens import PASSWORD_RESET, VERIFY_EMAIL, issue_token


@pytest.fixture()
def client(database):
    def override():
        yield database

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def captured_tokens(monkeypatch):
    values = {VERIFY_EMAIL: [], PASSWORD_RESET: []}
    monkeypatch.setattr(
        "app.api.v1.auth.AccountEmailService.verification",
        lambda _user, token: values[VERIFY_EMAIL].append(token) or "SENT",
    )
    monkeypatch.setattr(
        "app.api.v1.auth.AccountEmailService.password_reset",
        lambda _user, token: values[PASSWORD_RESET].append(token) or "SENT",
    )
    return values


def _login(client, email, password="StrongPass123"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _user(database, email, *, role="USER"):
    row = User(
        email=email,
        password_hash=hash_password("StrongPass123"),
        is_active=True,
        role=role,
        email_verified_at=datetime.now(timezone.utc),
    )
    database.add(row)
    database.commit()
    database.refresh(row)
    return row


def test_signup_verification_is_hashed_single_use_and_required(client, database, captured_tokens):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": "StrongPass123"},
    )
    assert response.status_code == 201
    assert response.json()["verification_required"] is True
    assert "set-cookie" not in response.headers
    assert _login(client, "new@example.com").status_code == 403

    raw = captured_tokens[VERIFY_EMAIL].pop()
    stored = database.query(UserAuthToken).filter_by(purpose=VERIFY_EMAIL).one()
    assert stored.token_hash != raw and len(stored.token_hash) == 64
    assert client.post("/api/v1/auth/verify-email", json={"token": raw}).status_code == 200
    assert client.post("/api/v1/auth/verify-email", json={"token": raw}).status_code == 400
    login = _login(client, "new@example.com")
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]


def test_expired_verification_and_enumeration_safe_resend(client, database, captured_tokens):
    client.post("/api/v1/auth/register", json={"email": "waiting@example.com", "password": "StrongPass123"})
    raw = captured_tokens[VERIFY_EMAIL].pop()
    token = database.query(UserAuthToken).filter_by(purpose=VERIFY_EMAIL).one()
    token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    database.commit()
    assert client.post("/api/v1/auth/verify-email", json={"token": raw}).status_code == 400

    known = client.post("/api/v1/auth/resend-verification", json={"email": "waiting@example.com"})
    unknown = client.post("/api/v1/auth/resend-verification", json={"email": "missing@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_password_reset_is_generic_single_use_and_invalidates_sessions(client, database, captured_tokens):
    user = _user(database, "reset@example.com")
    assert _login(client, user.email).status_code == 200
    generic = client.post("/api/v1/auth/forgot-password", json={"email": user.email})
    unknown = client.post("/api/v1/auth/forgot-password", json={"email": "unknown@example.com"})
    assert generic.json() == unknown.json()
    raw = captured_tokens[PASSWORD_RESET].pop()

    reset = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw, "password": "ChangedPass456"},
    )
    assert reset.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/reset-password", json={"token": raw, "password": "AnotherPass789"}).status_code == 400
    assert _login(client, user.email).status_code == 401
    assert _login(client, user.email, "ChangedPass456").status_code == 200
    database.refresh(user)
    assert verify_user_password(user.password_hash, "ChangedPass456")
    assert user.password_hash.startswith("$argon2id$")


def test_logout_revokes_redis_session_when_session_version_is_zero(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.values = {}

        def setex(self, key, _ttl, value):
            self.values[key] = value.encode()

        def get(self, key):
            return self.values.get(key)

        def delete(self, key):
            self.values.pop(key, None)

    fake = FakeRedis()
    monkeypatch.setattr(user_auth, "_redis_client", lambda: fake)
    user_auth._reset_user_session_store_for_tests()
    token = user_auth.create_user_session(123, session_version=0)
    assert user_auth._session_identity(token) == (123, 0)

    user_auth.revoke_user_session(token)

    assert user_auth._session_identity(token) is None


def test_issuing_new_reset_invalidates_previous_token(database):
    user = _user(database, "rotation@example.com")
    old = issue_token(database, user, PASSWORD_RESET)
    new = issue_token(database, user, PASSWORD_RESET)
    rows = database.query(UserAuthToken).filter_by(user_id=user.id, purpose=PASSWORD_RESET).all()
    assert old != new
    assert sum(row.used_at is None for row in rows) == 1


def test_support_is_private_and_admin_changes_are_visible_to_user(client, database):
    user = _user(database, "supporter@example.com")
    admin = _user(database, "owner@example.com", role="ADMIN")
    assert _login(client, user.email).status_code == 200
    assert client.get("/api/v1/support/me").status_code == 403
    assert client.get("/api/v1/admin/support/overview").status_code == 403
    client.post("/api/v1/auth/logout")

    assert _login(client, admin.email).status_code == 200
    enabled = client.post(f"/api/v1/admin/support/users/{user.id}/eligibility", json={"enabled": True})
    assert enabled.status_code == 200
    created = client.post(
        f"/api/v1/admin/support/users/{user.id}/contributions",
        json={
            "amount": "12.34",
            "currency": "SGD",
            "payment_method": "PayNow",
            "status": "CONFIRMED",
            "reference": "bank-ref",
            "admin_note": "private note",
        },
    )
    assert created.status_code == 200
    assert created.json()["amount"] == "12.34"
    client.post("/api/v1/auth/logout")

    assert _login(client, user.email).status_code == 200
    summary = client.get("/api/v1/support/me")
    history = client.get("/api/v1/support/history")
    assert summary.status_code == history.status_code == 200
    assert summary.json()["last_contribution"]["amount"] == "12.34"
    assert "admin_note" not in history.json()["items"][0]
    assert "payment_reference" not in history.json()["items"][0]
    assert client.get("/api/v1/support/paynow").status_code == 503
    assert database.query(SupportContribution).count() == 1
    assert database.query(SupportAuditLog).count() == 2


def test_discord_support_authorization_and_idempotency(client, database, monkeypatch):
    user = _user(database, "discord-user@example.com")
    monkeypatch.setattr(settings, "SUPPORT_DISCORD_SHARED_SECRET", "shared-secret")
    monkeypatch.setattr(settings, "SUPPORT_DISCORD_ADMIN_IDS", "42")
    monkeypatch.setattr(settings, "SUPPORT_DISCORD_ADMIN_ROLE_IDS", "")
    payload = {
        "interaction_id": "interaction-1",
        "actor_id": "42",
        "action": "add_contribution",
        "email": user.email,
        "contribution": {
            "amount": "9.99",
            "currency": "SGD",
            "payment_method": "PayNow",
            "status": "CONFIRMED",
        },
    }
    assert client.post("/api/v1/support/discord/actions", json=payload).status_code == 403
    headers = {"X-Discord-Support-Secret": "shared-secret"}
    first = client.post("/api/v1/support/discord/actions", json=payload, headers=headers)
    retry = client.post("/api/v1/support/discord/actions", json=payload, headers=headers)
    assert first.status_code == retry.status_code == 200
    assert retry.json()["deduplicated"] is True
    assert database.query(SupportContribution).count() == 1


def test_discord_support_repeated_auth_failures_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(settings, "SUPPORT_DISCORD_SHARED_SECRET", "shared-secret")
    monkeypatch.setattr(settings, "SUPPORT_DISCORD_ADMIN_IDS", "42")
    payload = {
        "interaction_id": "unauthorized-interaction",
        "actor_id": "42",
        "action": "status",
        "email": "nobody@example.com",
    }
    for _ in range(10):
        response = client.post(
            "/api/v1/support/discord/actions",
            json=payload,
            headers={"X-Discord-Support-Secret": "wrong-secret"},
        )
        assert response.status_code == 403

    blocked = client.post(
        "/api/v1/support/discord/actions",
        json=payload,
        headers={"X-Discord-Support-Secret": "wrong-secret"},
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


@pytest.mark.parametrize("amount", ["0", "-1", "NaN"])
def test_support_rejects_invalid_amount(client, database, amount):
    suffix = {"0": "zero", "-1": "negative", "NaN": "nan"}[amount]
    admin = _user(database, f"admin-{suffix}@example.com", role="ADMIN")
    user = _user(database, f"user-{suffix}@example.com")
    assert _login(client, admin.email).status_code == 200
    response = client.post(
        f"/api/v1/admin/support/users/{user.id}/contributions",
        json={"amount": amount, "currency": "SGD", "payment_method": "PayNow", "status": "CONFIRMED"},
    )
    assert response.status_code == 422
