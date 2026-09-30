"""Public maintenance status and authenticated administration coverage."""

from datetime import datetime, timedelta, timezone
import hashlib

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.database.connection import get_db
from app.main import app


def _client(database):
    def override_db():
        yield database

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _login(client, monkeypatch):
    salt = b"0123456789abcdef"
    password = "maintenance admin password"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(
        settings,
        "ADMIN_PASSWORD_HASH",
        f"pbkdf2_sha256$1000${salt.hex()}${digest}",
    )
    assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200


def _payload(start, end, title="OTT Tracker System Maintenance"):
    return {
        "title": title,
        "description": "Routine reliability and performance maintenance.",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "affected_services": ["Movie Search", "OTT Calendar"],
        "impact_level": "LOW",
        "impact_message": "Brief interruptions may occur.",
    }


def test_public_maintenance_states_and_admin_lifecycle(database, monkeypatch):
    client = _client(database)
    try:
        empty = client.get("/api/v1/maintenance")
        assert empty.status_code == 200
        assert empty.json()["overall_status"] == "OPERATIONAL"
        assert client.post("/api/v1/maintenance", json={}).status_code == 405
        assert client.get("/api/v1/admin/maintenance").status_code == 401

        _login(client, monkeypatch)
        now = datetime.now(timezone.utc)
        created = client.post(
            "/api/v1/admin/maintenance",
            json=_payload(now + timedelta(hours=4), now + timedelta(hours=5)),
        )
        assert created.status_code == 200
        item = created.json()
        assert item["stored_status"] == "SCHEDULED"
        assert client.get("/api/v1/maintenance").json()["overall_status"] == "SCHEDULED"

        active = client.patch(
            f"/api/v1/admin/maintenance/{item['id']}",
            json={
                "start_time": (now - timedelta(minutes=30)).isoformat(),
                "end_time": (now + timedelta(minutes=30)).isoformat(),
                "description": "Updated maintenance description.",
            },
        )
        assert active.status_code == 200
        public = client.get("/api/v1/maintenance").json()
        assert public["overall_status"] == "ACTIVE"
        assert public["active"][0]["status"] == "ACTIVE"
        assert public["active"][0]["description"] == "Updated maintenance description."

        completed = client.patch(
            f"/api/v1/admin/maintenance/{item['id']}", json={"status": "COMPLETED"}
        )
        assert completed.status_code == 200
        assert completed.json()["completed_at"]
        assert client.get("/api/v1/maintenance").json()["history"][0]["status"] == "COMPLETED"

        cancelled = client.post(
            "/api/v1/admin/maintenance",
            json=_payload(now + timedelta(days=1), now + timedelta(days=1, hours=1), "Cancelled work"),
        ).json()
        assert client.delete(f"/api/v1/admin/maintenance/{cancelled['id']}").status_code == 409
        assert client.patch(
            f"/api/v1/admin/maintenance/{cancelled['id']}", json={"status": "CANCELLED"}
        ).status_code == 200
        assert client.delete(f"/api/v1/admin/maintenance/{cancelled['id']}").status_code == 200
    finally:
        app.dependency_overrides.clear()
        client.close()


def test_maintenance_rejects_invalid_time_range(database, monkeypatch):
    client = _client(database)
    try:
        _login(client, monkeypatch)
        now = datetime.now(timezone.utc)
        response = client.post(
            "/api/v1/admin/maintenance",
            json=_payload(now + timedelta(hours=2), now + timedelta(hours=1)),
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        client.close()
