"""Operational Admin Control Center regression coverage."""

from datetime import date
import hashlib

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.database.connection import get_db
from app.main import app
from app.models.operations import AdminAuditLog, MovieRequest, OttEvidence, OttSourceRelease
from app.services.ott_source_sync import OttSourceSyncService


def _admin_client(database, monkeypatch):
    def override_db():
        yield database

    salt = b"0123456789abcdef"
    password = "admin control password"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(
        settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}"
    )
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200
    return client


def test_admin_control_center_endpoints_and_request_detail(database, monkeypatch):
    request = MovieRequest(
        request_id="REQ-CONTROL",
        movie_name="Example Film",
        verified_title="Example Film",
        external_movie_id=101,
        imdb_id="tt0000101",
        email="viewer@example.test",
        status="PENDING",
        local_movie_id=1,
        movie_existed_at_submission=True,
        confirmation_email_status="SENT",
        admin_notification_email_status="FAILED",
    )
    database.add(request)
    database.commit()
    client = _admin_client(database, monkeypatch)
    try:
        dashboard = client.get("/api/v1/admin/dashboard")
        assert dashboard.status_code == 200
        assert {
            "movies_added_today",
            "reviewing_requests",
            "ott_confirmed",
            "movies_missing_trailer",
            "failed_jobs",
            "alerts",
            "recent_activity",
            "sources",
        } <= set(dashboard.json())
        listing = client.get("/api/v1/admin/requests?email_status=FAILED&local=exists")
        assert listing.status_code == 200
        assert listing.json()["counters"]["ALL"] == 1
        assert listing.json()["items"][0]["sla"] == "NORMAL"
        detail = client.get("/api/v1/admin/requests/REQ-CONTROL").json()
        assert detail["local"]["exists"] is True
        assert detail["local_movie_id"] == 1
        assert "data_completeness" in detail and "ott" in detail and "trailer" in detail
        assert client.get("/api/v1/admin/movies?search=101").json()["items"][0]["tmdb_id"] == 101
        assert client.get("/api/v1/admin/ott-overview").status_code == 200
        assert client.get("/api/v1/admin/ott-releases").status_code == 200
        sources = client.get("/api/v1/admin/sources").json()
        assert {item["source"] for item in sources["items"]} >= {"tmdb", "ottplay", "justwatch", "tavily", "smtp", "youtube"}
        assert "api_key" not in str(sources).lower()
        health = client.get("/api/v1/admin/data-health").json()
        assert {"movies", "credits", "images", "ott", "trailers", "ratings", "requests", "comments", "jobs"} <= set(health["summary"])

        changed = client.patch("/api/v1/admin/requests/REQ-CONTROL", json={"status": "REVIEWING"})
        assert changed.status_code == 200
        assert database.query(AdminAuditLog).filter_by(action="request_status_changed").count() == 1
        assert client.get("/api/v1/admin/audit").json()["items"][0]["target_id"] == "REQ-CONTROL"
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_ott_source_adapter_is_resumable_matches_and_retains_unmatched(database, monkeypatch):
    monkeypatch.setattr(settings, "OTTPLAY_ENABLED", True)
    monkeypatch.setattr(settings, "OTTPLAY_ADAPTER_URL", "https://adapter.example.test/ottplay")
    monkeypatch.setattr(settings, "OTTPLAY_API_KEY", "secret-never-returned")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "pages_checked": 2,
                "items": [
                    {
                        "id": "known-101",
                        "tmdb_id": 101,
                        "title": "Example Film",
                        "platform": "Netflix",
                        "ott_release_date": date.today().isoformat(),
                        "language": "ml",
                        "source_url": "https://www.ottplay.com/example-film",
                    },
                    {
                        "id": "unknown-999",
                        "title": "No Local Match Film",
                        "platform": "Prime Video",
                        "ott_release_date": date.today().isoformat(),
                        "language": "ta",
                        "source_url": "https://www.ottplay.com/no-local-match",
                    },
                ],
            }

    monkeypatch.setattr("app.services.ott_source_sync.httpx.get", lambda *args, **kwargs: Response())
    result = OttSourceSyncService(database, "ottplay").sync()
    assert result["status"] == "COMPLETE"
    assert result["stats"]["movies_discovered"] == 2
    assert result["stats"]["movies_matched"] == 1
    assert result["stats"]["movies_unmatched"] == 1
    assert database.query(OttSourceRelease).filter_by(status="MATCHED", matched_movie_id=1).count() == 1
    assert database.query(OttSourceRelease).filter_by(status="UNMATCHED").count() == 1
    evidence = database.query(OttEvidence).filter_by(movie_id=1, source_type="ottplay").one()
    assert evidence.status == "POSSIBLE"
    # A second sync updates the durable rows and does not duplicate evidence.
    OttSourceSyncService(database, "ottplay").sync()
    assert database.query(OttSourceRelease).count() == 2
    assert database.query(OttEvidence).filter_by(movie_id=1, source_type="ottplay").count() == 1


def test_justwatch_adapter_failure_is_isolated_and_secret_safe(database, monkeypatch):
    monkeypatch.setattr(settings, "JUSTWATCH_ENABLED", True)
    monkeypatch.setattr(settings, "JUSTWATCH_ADAPTER_URL", "https://adapter.example.test/justwatch")
    monkeypatch.setattr(settings, "JUSTWATCH_API_KEY", "never-expose-this-key")

    def fail(*args, **kwargs):
        raise RuntimeError("adapter rejected Authorization: Bearer never-expose-this-key")

    monkeypatch.setattr("app.services.ott_source_sync.httpx.get", fail)
    result = OttSourceSyncService(database, "justwatch").sync()
    assert result["failed"] is True
    assert result["status"] == "FAILED"
    assert "never-expose-this-key" not in result["last_error"]
    # The failed optional adapter does not remove previously ingested rows or
    # prevent the admin API from reporting its state.
    assert database.query(OttSourceRelease).count() == 0
