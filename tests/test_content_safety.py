from datetime import date
import hashlib

from fastapi.testclient import TestClient

from app.models.content_safety import ContentSafetyAudit
from app.config.settings import settings
from app.database.connection import get_db
from app.main import app
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieReleaseDate
from app.repositories.movie_repository import MovieRepository
from app.services.content_safety import ContentSafetyService
from app.services.notification_service import NotificationService


def _movie(database, tmdb_id: int, title: str, *, adult: bool = False) -> Movie:
    movie = Movie(
        tmdb_id=tmdb_id,
        title=title,
        original_title=f"{title} Original",
        release_date=date(2026, 1, 2),
        original_language="ml",
        adult=adult,
        safety_classification="SAFE",
        safety_public_visibility="FULL",
    )
    database.add(movie)
    database.flush()
    database.add(ExternalId(movie_id=movie.id, provider="imdb", external_id=f"tt{tmdb_id}"))
    database.commit()
    database.refresh(movie)
    return movie


def test_conservative_classification_and_change_only_history(database, monkeypatch):
    service = ContentSafetyService(database)
    notifications = []
    monkeypatch.setattr(ContentSafetyService, "notify_audit", lambda _self, audit_id: notifications.append(audit_id) or True)

    general = _movie(database, 801, "General Film")
    assert service.apply_automatic(general) is None
    assert general.safety_classification == "SAFE"
    assert database.query(ContentSafetyAudit).filter_by(movie_id=general.id).count() == 0

    mature = _movie(database, 802, "Mature Film")
    database.add(MovieReleaseDate(movie_id=mature.id, country="IN", release_date=date(2026, 1, 2), release_type="3", certification="18"))
    database.commit()
    assert service.apply_automatic(mature) is None
    assert mature.safety_classification == "MATURE"
    assert mature.safety_public_visibility == "FULL"

    restricted = _movie(database, 803, "Restricted Film")
    database.add(MovieReleaseDate(movie_id=restricted.id, country="IN", release_date=date(2026, 1, 2), release_type="3", certification="X"))
    database.commit()
    first = service.apply_automatic(restricted)
    assert first.new_classification == "RESTRICTED"
    assert restricted.safety_public_visibility == "DIRECT_ONLY"
    restricted.adult = True
    second = service.apply_automatic(restricted)
    assert second.previous_classification == "RESTRICTED"
    assert second.new_classification == "BLOCKED"
    assert restricted.safety_public_visibility == "HIDDEN"
    assert service.apply_automatic(restricted) is None

    restored = service.apply_manual(restricted, "SAFE", "Administrator verified the title")
    assert restored.action == "RESTORED"
    assert restricted.safety_public_visibility == "FULL"
    assert restricted.safety_manual_override is True
    assert database.query(ContentSafetyAudit).filter_by(movie_id=restricted.id).count() == 3
    assert len(notifications) == 3
    assert first.tmdb_id == 803 and first.imdb_id == "tt803"
    assert first.title == "Restricted Film" and first.classification_source == "AUTOMATIC"


def test_same_state_manual_audit_does_not_alert(database, monkeypatch):
    movie = _movie(database, 804, "Reviewed Film")
    service = ContentSafetyService(database)
    monkeypatch.setattr(ContentSafetyService, "notify_audit", lambda *_args: (_ for _ in ()).throw(AssertionError("must not alert")))
    audit = service.apply_manual(movie, "SAFE", "Administrator retained safe classification")
    assert audit.notification_status == "NOT_REQUIRED"
    assert audit.public_visibility_before == audit.public_visibility_after == "FULL"


def test_discord_failure_isolated_and_queued_for_bounded_retry(database, monkeypatch):
    movie = _movie(database, 805, "Blocked Film", adult=True)
    queued = []
    monkeypatch.setattr(NotificationService, "discord_method", staticmethod(lambda: {"configured": True, "method": "EXISTING_BOT"}))
    monkeypatch.setattr(NotificationService, "notify", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(ContentSafetyService, "_enqueue_notification", staticmethod(lambda task, args: queued.append((task, args))))

    audit = ContentSafetyService(database).apply_automatic(movie)
    database.refresh(movie)
    database.refresh(audit)
    assert movie.safety_classification == "BLOCKED"
    assert movie.safety_public_visibility == "HIDDEN"
    assert audit.notification_status == "FAILED"
    assert audit.notification_attempts == 1
    assert queued == [("content_safety.notification", [audit.id])]


def test_safe_to_blocked_sends_one_complete_discord_alert(database, monkeypatch):
    movie = _movie(database, 807, "Immediate Block Film", adult=True)
    delivered = []
    monkeypatch.setattr(
        NotificationService,
        "notify",
        lambda _self, message, **kwargs: delivered.append((message, kwargs)) or True,
    )
    audit = ContentSafetyService(database).apply_automatic(movie)
    assert len(delivered) == 1
    message, options = delivered[0]
    assert "BLOCKED FROM PUBLIC CATALOGUE" in message
    assert "Title: Immediate Block Film" in message
    assert "TMDB ID: 807" in message
    assert "Previous Status: SAFE" in message
    assert "New Status: BLOCKED" in message
    assert "Public Visibility: HIDDEN" in message
    assert options["channels"] == ("discord",)
    assert audit.notification_status == "SENT"


def test_bulk_scan_sends_one_digest_and_public_repository_hides_non_full(database, monkeypatch):
    first = database.query(Movie).filter_by(tmdb_id=101).one()
    second = database.query(Movie).filter_by(tmdb_id=102).one()
    first.adult = second.adult = True
    first.safety_classification = second.safety_classification = "SAFE"
    first.safety_public_visibility = second.safety_public_visibility = "FULL"
    database.commit()
    digests = []
    monkeypatch.setattr(ContentSafetyService, "notify_digest", lambda _self, ids, scanned: digests.append((ids, scanned)) or True)

    result = ContentSafetyService(database).scan_batch(500, bulk=True)
    assert result["blocked"] == 2
    assert len(digests) == 1 and len(digests[0][0]) == 2
    assert MovieRepository(database).get_all() == []
    assert MovieRepository(database).get_by_id(first.id) is None
    assert database.get(Movie, first.id) is not None


def test_restricted_movie_is_direct_only(database):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    movie.safety_classification = "RESTRICTED"
    movie.safety_public_visibility = "DIRECT_ONLY"
    database.commit()
    repository = MovieRepository(database)
    assert movie not in repository.get_all()
    assert repository.get_by_id(movie.id).id == movie.id


def test_admin_history_lists_hidden_movies_and_restores_them(database, monkeypatch):
    movie = _movie(database, 806, "Admin Safety Film", adult=True)
    monkeypatch.setattr(ContentSafetyService, "notify_audit", lambda *_args: True)
    ContentSafetyService(database).apply_automatic(movie)

    def override_db():
        yield database

    salt = b"0123456789abcdef"
    password = "content safety admin"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}")
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200
        listing = client.get("/api/v1/admin/content-safety?state=BLOCKED")
        assert listing.status_code == 200
        assert listing.json()["items"][0]["movie_id"] == movie.id
        assert listing.json()["items"][0]["tmdb_adult"] is True
        detail = client.get(f"/api/v1/admin/content-safety/movies/{movie.id}")
        assert detail.status_code == 200
        assert detail.json()["movie"]["public_visibility"] == "HIDDEN"
        restored = client.patch(
            f"/api/v1/admin/content-safety/movies/{movie.id}",
            json={"classification": "SAFE", "reason": "Administrator confirmed public restoration"},
        )
        assert restored.status_code == 200
        database.refresh(movie)
        assert movie.safety_public_visibility == "FULL"
        assert client.get("/api/v1/admin/content-safety?state=RESTORED").json()["total"] == 1
    finally:
        client.close()
        app.dependency_overrides.clear()
