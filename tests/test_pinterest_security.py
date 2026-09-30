import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.database.connection import get_db
from app.main import app
from app.models.pinterest import PinterestOAuthToken
from app.services.pinterest.oauth_state import _reset_oauth_states_for_tests


@pytest.fixture()
def client(database):
    def override_db():
        yield database

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_pinterest_oauth_state_is_single_use_and_callback_url_is_clean(
    client, database, monkeypatch
):
    _reset_oauth_states_for_tests()
    salt = b"pinterest-test-salt"
    password = "correct horse battery staple"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1_000).hex()
    monkeypatch.setattr(
        settings,
        "ADMIN_PASSWORD_HASH",
        f"pbkdf2_sha256$1000${salt.hex()}${digest}",
    )
    monkeypatch.setattr(settings, "PINTEREST_APP_ID", "test-client")
    monkeypatch.setattr(settings, "PINTEREST_APP_SECRET", "test-secret")
    monkeypatch.setattr(
        "app.services.pinterest.client.PinterestClient.exchange_code",
        lambda _self, _code: {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "access_token_expiration": 3600,
            "scope": "boards:read",
        },
    )
    assert client.post(
        "/api/v1/admin/login", json={"password": password}
    ).status_code == 200
    auth_url = client.get("/api/v1/pinterest/auth/url").json()["url"]
    state = parse_qs(urlparse(auth_url).query)["state"][0]

    callback = client.get(
        "/api/v1/integrations/pinterest/callback",
        params={"code": "temporary-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/admin/pinterest?oauth=connected"
    assert "temporary-code" not in callback.headers["location"]
    assert database.query(PinterestOAuthToken).filter_by(is_active=True).count() == 1

    replay = client.get(
        "/api/v1/integrations/pinterest/callback",
        params={"code": "temporary-code", "state": state},
        follow_redirects=False,
    )
    assert replay.headers["location"] == "/admin/pinterest?oauth=failed"
    assert database.query(PinterestOAuthToken).filter_by(is_active=True).count() == 1
