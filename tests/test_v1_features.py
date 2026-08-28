"""Offline integration coverage for the movie-only V1 public and admin flows."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import httpx

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import settings
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.genre import Genre
from app.models.language import Language
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, Keyword, MovieCredit, MovieImage, MovieKeyword, MovieRating, MovieReleaseDate, Person
from app.models.operations import MovieRequest, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.operations import OttResearchService
from app.services.release_status import ReleaseStatusService
from app.services.movie_requests import MovieRequestAutomationService, MovieRequestEmailService


def verified_snapshot(movie_id=999, title="Verified Missing Film"):
    return {
        "external_movie_id": movie_id,
        "verified_title": title,
        "original_title": "Verified Original",
        "release_date": "2026-01-02",
        "original_language": "ml",
        "language_name": "Malayalam",
        "poster_path": "/verified.jpg",
        "backdrop_path": "/verified-backdrop.jpg",
        "overview": "Verified overview",
        "genres": ["Drama"],
        "status": "Released",
        "imdb_id": "tt7654321",
        "director": "Verified Director",
    }


@pytest.fixture()
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    today = date.today()
    genre = Genre(tmdb_id=18, name="Drama", slug="drama")
    language = Language(iso_639_1="ml", english_name="Malayalam", native_name="മലയാളം")
    actor = Person(tmdb_id=10, name="Example Actor", profile_path="/actor.jpg", known_for_department="Acting")
    director = Person(tmdb_id=11, name="Example Director", profile_path="/director.jpg", known_for_department="Directing")
    movie = Movie(tmdb_id=101, title="Example Film", original_title="Original Example", overview="A real stored overview.", release_date=today, poster_path="/poster.jpg", backdrop_path="/backdrop.jpg", popularity=90, vote_average=8.2, vote_count=120, original_language="ml", status="Released", tagline="A stored tagline", runtime_minutes=130, genres=[genre], languages=[language])
    future = Movie(tmdb_id=102, title="Future Film", release_date=today + timedelta(days=20), popularity=30, vote_average=7, original_language="ml", genres=[genre], languages=[language])
    session.add_all([movie, future, actor, director]); session.flush()
    session.add_all([
        MovieCredit(movie_id=movie.id, person_id=actor.id, credit_type="cast", character="Hero", cast_order=0),
        MovieCredit(movie_id=movie.id, person_id=director.id, credit_type="crew", department="Directing", job="Director"),
        MovieCredit(movie_id=movie.id, person_id=director.id, credit_type="crew", department="Camera", job="Director of Photography"),
        OttAvailability(movie_id=movie.id, provider="Netflix", provider_logo="/netflix.png", ott_release_date=today, status="confirmed", source_type="official", source_url="https://netflix.com/title/example", confidence=95),
        MovieReleaseDate(movie_id=movie.id, country="IN", release_date=today, release_type="theatrical", certification="U/A"),
        MovieReleaseDate(movie_id=future.id, country="IN", release_date=today + timedelta(days=20), release_type="3"),
        MovieImage(movie_id=movie.id, image_type="logo", source="tmdb", source_id="logo", original_url="https://image.tmdb.org/logo.png"),
        MovieRating(movie_id=movie.id, source="imdb", rating=8, vote_count=100),
        ExternalId(movie_id=movie.id, provider="imdb", external_id="tt1234567"),
    ])
    keyword = Keyword(tmdb_id=20, name="friendship"); session.add(keyword); session.flush(); session.add(MovieKeyword(movie_id=movie.id, keyword_id=keyword.id))
    session.commit()
    ReleaseStatusService(session).classify_batch(100)
    try: yield session
    finally: session.close(); engine.dispose()


@pytest.fixture()
def client(database):
    def override():
        yield database
    app.dependency_overrides[get_db] = override
    try: yield TestClient(app)
    finally: app.dependency_overrides.clear()


def test_home_discover_search_and_browse(client):
    home = client.get("/api/v1/home").json()
    assert home["trending"][0]["title"] == "Example Film"
    assert home["trending"][0]["rating"] == 8
    assert home["trending"][0]["rating_source"] == "IMDb"
    assert "tmdb_id" not in home["trending"][0]
    assert "display_id" not in home["trending"][0]
    assert home["language_sections"]["ml"]["items"]
    assert home["platforms"][0]["name"] == "Netflix"
    assert client.get("/api/v1/discover?genre=drama&language=ml&director=Example&rating=8&sort=name-asc").json()["total"] == 1
    assert client.get("/api/v1/discover?cinematographer=Example%20Director").json()["items"][0]["title"] == "Example Film"
    assert client.get("/api/v1/discover?release_status=released").json()["total"] == 1
    assert client.get("/api/v1/discover?release_status=upcoming").json()["total"] == 1
    search = client.get("/api/v1/search?q=Example").json()
    assert search["movies"]["total"] == 1 and search["people"]["total"] == 2


def test_language_filter_uses_original_language_not_spoken_language(client, database):
    english = Language(iso_639_1="en", english_name="English")
    database.add(english)
    database.query(Movie).filter(Movie.tmdb_id == 101).one().languages.append(english)
    database.commit()

    assert client.get("/api/v1/discover?language=ml").json()["total"] == 2
    assert client.get("/api/v1/discover?language=en").json()["total"] == 0


def test_public_movie_detail_neutralizes_provider_status(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.public.research_status_label",
        lambda *_args: "Awaiting TMDB release information",
    )

    detail = client.get("/api/v1/movies/1/detail").json()
    assert detail["movie"]["ott_research_status"] == "Awaiting external metadata release information"


def test_calendar_ott_movie_and_person(client, database):
    database.add(OttAvailability(movie_id=2, provider="RumourTV", ott_release_date=date.today(), status="announced", confidence=30))
    database.commit()
    for period in ("previous-week", "this-week", "next-week", "previous-month", "this-month", "next-month"):
        response = client.get(f"/api/v1/calendar/{period}")
        assert response.status_code == 200
        assert set(response.json()["theatrical"]) == {"items", "total"}
        assert set(response.json()["ott"]) == {"items", "total"}
    calendar = client.get("/api/v1/calendar/this-week").json()
    assert calendar["theatrical"]["items"][0]["certification"] == "U/A"
    assert calendar["ott"]["items"][0]["ott_platform"] == "Netflix"
    assert calendar["ott"]["items"][0]["rating"] == 8
    assert all(item["ott_platform"] != "RumourTV" for item in calendar["ott"]["items"])
    assert client.get("/api/v1/calendar/not-real").status_code == 404
    assert client.get("/api/v1/ott").json()["confirmed"][0]["title"] == "Example Film"
    platform = client.get("/api/v1/ott/netflix").json()
    assert platform["total"] == 1 and platform["platform"] == "Netflix"
    detail = client.get("/api/v1/movies/1/detail").json()
    assert detail["movie"]["certification"] == "U/A"
    assert detail["movie"]["rating"] == 8 and detail["movie"]["rating_source"] == "IMDb"
    assert "tmdb_id" not in detail["movie"]
    assert detail["movie"]["display_id"] == 101
    assert detail["movie"]["id"] == 1
    assert detail["movie"]["release_status"] == "Released"
    assert detail["movie"]["theatrical_release_date"] == date.today().isoformat()
    assert detail["movie"]["ott_platform"] == "Netflix"
    assert detail["movie"]["ott_release_date"] == date.today().isoformat()
    assert all(item["provider"].lower() != "tmdb" for item in detail["external_ids"])
    assert detail["external_ids"][0]["url"] == "https://www.imdb.com/title/tt1234567/"
    assert detail["crew_by_role"]["cinematography"][0]["job"] == "Director of Photography"
    assert detail["cast"][0]["profile_path"] == "/actor.jpg"
    assert detail["keywords"] == ["friendship"]
    person = client.get("/api/v1/people/1?credit_type=cast&sort=oldest").json()
    assert "tmdb_id" not in person
    assert person["display_id"] == 10
    assert person["id"] == 1
    assert person["filmography"][0]["character"] == "Hero"
    assert person["filmography"][0]["normalized_role"] == "actor"


def test_missing_imdb_rating_is_not_fabricated_and_missing_ott_is_queued(client, database):
    future = client.get("/api/v1/discover?q=Future").json()["items"][0]
    assert future["rating"] is None and future["rating_source"] is None
    assert OttResearchService(database).queue_missing() == 0
    assert database.get(Movie, 2).ott_research_eligibility == "WAITING_RELEASE"
    assert database.query(OttEvidence).filter_by(movie_id=2, status="QUEUED").count() == 0


def test_confirmed_research_publishes_to_canonical_ott_calendar(client, database):
    released = Movie(tmdb_id=103, title="Released Missing OTT", original_language="ml")
    database.add(released); database.flush()
    database.add(MovieReleaseDate(movie_id=released.id, country="IN", release_date=date.today() - timedelta(days=10), release_type="3"))
    database.commit()
    assert OttResearchService(database).queue_missing() == 1
    evidence = OttResearchService(database).record_evidence(
        released.id,
        platform="Prime Video",
        release_date=date.today(),
        source_url="https://primevideo.com/detail/example",
        confidence=95,
        source_rank="official_platform",
    )
    assert evidence.status == "CONFIRMED"
    calendar = client.get("/api/v1/calendar/this-week").json()
    assert any(item["id"] == released.id and item["ott_platform"] == "Prime Video" for item in calendar["ott"]["items"])


def test_request_admin_auth_and_management(client, database, monkeypatch):
    salt = b"0123456789abcdef"; password = "correct horse"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    monkeypatch.setattr("app.services.notification_service.NotificationService.notify", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.api.v1.operations.DeepSearchService.verify_movie", lambda _self, movie_id: verified_snapshot(movie_id))
    response = client.post("/api/v1/movie-requests", json={"movie_name": "Missing Film", "email": "viewer@example.com", "movie_external_id": 999})
    assert response.status_code == 201
    assert client.get("/api/v1/admin/dashboard").status_code == 401
    assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200
    listing = client.get("/api/v1/admin/requests?search=Missing").json()
    request_id = listing["items"][0]["request_id"]
    assert listing["items"][0]["movie_external_id"] == 999
    assert client.patch(f"/api/v1/admin/requests/{request_id}", json={"status": "REVIEWING"}).json()["status"] == "REVIEWING"
    assert client.patch(f"/api/v1/admin/requests/{request_id}", json={"status": "ADDED"}).status_code == 409
    stored = database.query(MovieRequest).filter_by(request_id=request_id).one()
    stored.confirmation_email_last_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    database.commit()
    retry = client.post(f"/api/v1/admin/requests/{request_id}/emails/confirmation/retry")
    assert retry.status_code == 200 and retry.json()["status"] == "NOT_CONFIGURED"
    assert client.get("/api/v1/admin/data-health").status_code == 200
    assert client.get("/api/v1/admin/images").status_code == 200
    ott_admin = client.get("/api/v1/admin/ott-research")
    assert ott_admin.status_code == 200
    assert {"release_status", "theatrical_release_date", "eligibility", "eligibility_label"} <= set(ott_admin.json()["items"][0])
    assert ott_admin.json()["daily_usage"]["limit"] == settings.OTT_DAILY_RESEARCH_MOVIE_LIMIT
    assert ott_admin.json()["tavily_usage"]["limit"] == settings.TAVILY_MONTHLY_APP_BUDGET
    assert client.get("/api/v1/admin/jobs").status_code == 200
    assert client.get("/api/v1/admin/notifications").status_code == 200


def test_movie_request_external_id_validation_duplicates_and_local_match(client, monkeypatch):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.notification_service.NotificationService.notify", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.api.v1.operations.DeepSearchService.verify_movie", lambda _self, movie_id: verified_snapshot(movie_id))
    base = {"movie_name": "Missing Film", "email": "viewer@example.com"}
    assert client.post("/api/v1/movie-requests", json=base).status_code == 422
    assert client.post("/api/v1/movie-requests", json=base | {"movie_external_id": 0}).status_code == 422
    assert client.post("/api/v1/movie-requests", json=base | {"movie_external_id": "tt1234567"}).status_code == 422
    local = client.post("/api/v1/movie-requests", json=base | {"movie_external_id": 101})
    assert local.status_code == 409
    assert local.json() == {"detail": "This movie is already available.", "local_movie_id": 1}
    accepted = client.post("/api/v1/movie-requests", json=base | {"movie_external_id": 998})
    assert accepted.status_code == 201 and accepted.json()["movie_external_id"] == 998
    duplicate = client.post("/api/v1/movie-requests", json={**base, "email": "other@example.com", "movie_external_id": 998})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "This movie has already been requested and is being processed."


def test_invalid_date_range(client):
    assert client.get("/api/v1/discover?date_from=2026-02-02&date_to=2026-01-01").status_code == 422


def test_request_verifies_and_persists_authoritative_snapshot(client, database, monkeypatch):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.notification_service.NotificationService.notify", lambda *args, **kwargs: True)
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda _self, movie_id: verified_snapshot(movie_id, "L2: Empuraan"),
    )
    response = client.post(
        "/api/v1/movie-requests",
        json={"movie_name": "Empuraan", "email": "viewer@example.com", "movie_external_id": 997},
    )
    assert response.status_code == 201
    assert response.json()["verified_title"] == "L2: Empuraan"
    assert response.json()["confirmation_email_status"] == "NOT_CONFIGURED"
    item = database.query(MovieRequest).filter_by(external_movie_id=997).one()
    assert item.movie_name == item.verified_title == "L2: Empuraan"
    assert item.original_title == "Verified Original"
    assert item.verified_release_date == date(2026, 1, 2)
    assert item.release_year == 2026
    assert item.verified_original_language == "ml"
    assert item.verified_language_name == "Malayalam"
    assert item.poster_path == "/verified.jpg"
    assert item.imdb_id == "tt7654321"
    assert item.director == "Verified Director"


def test_request_rejects_missing_or_unavailable_live_movie_without_writes(client, database, monkeypatch):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    before = database.query(MovieRequest).count()
    provider_request = httpx.Request("GET", "https://metadata.invalid/movie/996")
    not_found = httpx.HTTPStatusError("not found", request=provider_request, response=httpx.Response(404, request=provider_request))
    monkeypatch.setattr("app.api.v1.operations.DeepSearchService.verify_movie", lambda *_: (_ for _ in ()).throw(not_found))
    missing = client.post("/api/v1/movie-requests", json={"movie_name": "Missing", "email": "viewer@example.com", "movie_external_id": 996})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Movie could not be found. Please check the ID or use Deep Search."
    monkeypatch.setattr("app.api.v1.operations.DeepSearchService.verify_movie", lambda *_: (_ for _ in ()).throw(TimeoutError("secret provider timeout")))
    unavailable = client.post("/api/v1/movie-requests", json={"movie_name": "Missing", "email": "viewer@example.com", "movie_external_id": 995})
    assert unavailable.status_code == 503
    assert "provider" not in unavailable.json()["detail"].lower()
    assert database.query(MovieRequest).count() == before


@pytest.mark.parametrize("active_status", ["PENDING", "REVIEWING", "FOUND"])
def test_request_duplicate_protection_includes_found(client, database, monkeypatch, active_status):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    database.add(MovieRequest(request_id=f"REQ-{active_status}", movie_name="Verified", email="first@example.com", external_movie_id=994, status=active_status))
    database.commit()
    response = client.post("/api/v1/movie-requests", json={"movie_name": "Again", "email": "other@example.com", "movie_external_id": 994})
    assert response.status_code == 409
    assert response.json() == {"detail": "This movie has already been requested and is being processed.", "status": active_status}
    assert "email" not in response.text


def test_rejected_request_can_be_submitted_again(client, database, monkeypatch):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.notification_service.NotificationService.notify", lambda *args, **kwargs: True)
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    database.add(MovieRequest(request_id="REQ-REJECTED", movie_name="Old", email="first@example.com", external_movie_id=993, status="REJECTED"))
    database.commit()
    monkeypatch.setattr("app.api.v1.operations.DeepSearchService.verify_movie", lambda _self, movie_id: verified_snapshot(movie_id))
    response = client.post("/api/v1/movie-requests", json={"movie_name": "Again", "email": "other@example.com", "movie_external_id": 993})
    assert response.status_code == 201
    assert database.query(MovieRequest).filter_by(external_movie_id=993).count() == 2


def test_confirmation_email_tracking_failure_retry_and_idempotency(database, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    item = MovieRequest(request_id="REQ-EMAIL", movie_name="Verified", verified_title="Verified", email="viewer@example.com", external_movie_id=992)
    database.add(item); database.commit()
    attempts = []
    monkeypatch.setattr(MovieRequestEmailService, "_deliver", staticmethod(lambda _message: (_ for _ in ()).throw(OSError("SMTP unavailable"))))
    failed = MovieRequestEmailService(database).send(item, "confirmation", respect_cooldown=False)
    assert failed["status"] == "FAILED"
    assert item.confirmation_email_sent_at is None
    item.confirmation_email_last_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    database.commit()
    monkeypatch.setattr(MovieRequestEmailService, "_deliver", staticmethod(lambda message: attempts.append(message)))
    sent = MovieRequestEmailService(database).send(item, "confirmation")
    assert sent["status"] == "SENT" and item.confirmation_email_sent_at
    assert "aim to add it within 48 hours" in attempts[0].get_body(preferencelist=("plain",)).get_content()
    assert MovieRequestEmailService(database).send(item, "confirmation")["skipped"] == "already_sent"
    assert len(attempts) == 1


def test_reconciliation_marks_added_and_sends_completion_once(database, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    monkeypatch.setattr(settings, "SITE_URL", "https://movies.example.test")
    request = MovieRequest(request_id="REQ-ADD", movie_name="Requested", verified_title="Requested", email="viewer@example.com", external_movie_id=991, status="FOUND")
    movie = Movie(tmdb_id=991, title="Requested", original_language="ml")
    database.add_all([request, movie]); database.commit()
    messages = []
    monkeypatch.setattr(MovieRequestEmailService, "_deliver", staticmethod(lambda message: messages.append(message)))
    service = MovieRequestAutomationService(database)
    assert service.reconcile()["completed"] == 1
    assert request.status == "ADDED" and request.local_movie_id == movie.id
    assert request.completion_email_status == "SENT" and request.completion_email_sent_at
    assert f"https://movies.example.test/movies/{movie.id}" in messages[0].get_body(preferencelist=("plain",)).get_content()
    assert service.reconcile()["completed"] == 0
    assert len(messages) == 1


def test_rejection_email_and_sla_reminders_are_once_only(database, monkeypatch):
    now = datetime.now(timezone.utc)
    rejected = MovieRequest(request_id="REQ-NO", movie_name="No Movie", verified_title="No Movie", email="viewer@example.com", external_movie_id=990, status="REJECTED", public_rejection_reason="Rights information is unavailable.")
    database.add(rejected); database.commit()
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    messages = []
    monkeypatch.setattr(MovieRequestEmailService, "_deliver", staticmethod(lambda message: messages.append(message)))
    email = MovieRequestEmailService(database)
    assert email.send(rejected, "rejection", respect_cooldown=False)["status"] == "SENT"
    assert email.send(rejected, "rejection")["skipped"] == "already_sent"
    assert "Rights information is unavailable" in messages[0].get_body(preferencelist=("plain",)).get_content()

    rows = [
        MovieRequest(request_id="REQ-35", movie_name="35", email="a@example.com", external_movie_id=981, status="PENDING", created_at=now - timedelta(hours=35)),
        MovieRequest(request_id="REQ-36", movie_name="36", email="b@example.com", external_movie_id=982, status="PENDING", created_at=now - timedelta(hours=36, minutes=1)),
        MovieRequest(request_id="REQ-47", movie_name="47", email="c@example.com", external_movie_id=983, status="REVIEWING", created_at=now - timedelta(hours=47)),
        MovieRequest(request_id="REQ-48", movie_name="48", email="d@example.com", external_movie_id=984, status="FOUND", created_at=now - timedelta(hours=48, minutes=1)),
        MovieRequest(request_id="REQ-DONE", movie_name="Done", email="e@example.com", external_movie_id=985, status="ADDED", created_at=now - timedelta(hours=60)),
        MovieRequest(request_id="REQ-REJ", movie_name="Rejected", email="f@example.com", external_movie_id=986, status="REJECTED", created_at=now - timedelta(hours=60)),
    ]
    database.add_all(rows); database.commit()
    notices = []
    monkeypatch.setattr("app.services.movie_requests.NotificationService.notify", lambda _self, message, *args, **kwargs: notices.append(message) or True)
    service = MovieRequestAutomationService(database)
    result = service.check_sla(now)
    assert result == {"checked": 3, "warnings": 3, "escalations": 1}
    assert rows[0].sla_36_notified_at is None
    assert rows[1].sla_36_notified_at and rows[2].sla_36_notified_at
    assert rows[3].sla_36_notified_at and rows[3].sla_48_notified_at
    assert rows[4].sla_36_notified_at is None and rows[5].sla_48_notified_at is None
    assert service.check_sla(now) == {"checked": 3, "warnings": 0, "escalations": 0}
    assert len(notices) == 4
