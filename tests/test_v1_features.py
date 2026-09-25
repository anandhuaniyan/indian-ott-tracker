"""Offline integration coverage for the movie-only V1 public and admin flows."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import httpx
import smtplib

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
from app.models.movie_metadata import (
    ExternalId,
    Keyword,
    MovieCredit,
    MovieImage,
    MovieKeyword,
    MovieRating,
    MovieReleaseDate,
    MovieTrailer,
    Person,
)
from app.models.operations import (
    ContactRequest,
    MovieComment,
    MovieRequest,
    OttEvidence,
)
from app.models.ott_availability import OttAvailability
from app.models.research import RequestCommunication
from app.services.operations import OttResearchService
from conftest import register_sqlite_trigram_similarity
from app.services.release_status import ReleaseStatusService, site_date
from app.services.movie_requests import (
    MovieRequestAutomationService,
    MovieRequestEmailService,
)
from app.services.trailers import TrailerService


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
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    register_sqlite_trigram_similarity(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    today = site_date()
    genre = Genre(tmdb_id=18, name="Drama", slug="drama")
    language = Language(iso_639_1="ml", english_name="Malayalam", native_name="മലയാളം")
    actor = Person(
        tmdb_id=10,
        name="Example Actor",
        profile_path="/actor.jpg",
        known_for_department="Acting",
    )
    director = Person(
        tmdb_id=11,
        name="Example Director",
        profile_path="/director.jpg",
        known_for_department="Directing",
    )
    movie = Movie(
        tmdb_id=101,
        title="Example Film",
        original_title="Original Example",
        overview="A real stored overview.",
        release_date=today,
        poster_path="/poster.jpg",
        backdrop_path="/backdrop.jpg",
        popularity=90,
        vote_average=8.2,
        vote_count=120,
        original_language="ml",
        status="Released",
        tagline="A stored tagline",
        runtime_minutes=130,
        genres=[genre],
        languages=[language],
    )
    future = Movie(
        tmdb_id=102,
        title="Future Film",
        release_date=today + timedelta(days=20),
        popularity=30,
        vote_average=7,
        original_language="ml",
        genres=[genre],
        languages=[language],
    )
    session.add_all([movie, future, actor, director])
    session.flush()
    session.add_all(
        [
            MovieCredit(
                movie_id=movie.id,
                person_id=actor.id,
                credit_type="cast",
                character="Hero",
                cast_order=0,
            ),
            MovieCredit(
                movie_id=movie.id,
                person_id=director.id,
                credit_type="crew",
                department="Directing",
                job="Director",
            ),
            MovieCredit(
                movie_id=movie.id,
                person_id=director.id,
                credit_type="crew",
                department="Camera",
                job="Director of Photography",
            ),
            OttAvailability(
                movie_id=movie.id,
                provider="Netflix",
                provider_logo="/netflix.png",
                ott_release_date=today,
                status="released",
                source_type="official",
                source_url="https://netflix.com/title/example",
                confidence=95,
                verification_status="CONFIRMED",
            ),
            MovieReleaseDate(
                movie_id=movie.id,
                country="IN",
                release_date=today,
                release_type="theatrical",
                certification="U/A",
            ),
            MovieReleaseDate(
                movie_id=future.id,
                country="IN",
                release_date=today + timedelta(days=20),
                release_type="3",
            ),
            MovieImage(
                movie_id=movie.id,
                image_type="logo",
                source="tmdb",
                source_id="logo",
                original_url="https://image.tmdb.org/logo.png",
            ),
            MovieRating(movie_id=movie.id, source="imdb", rating=8, vote_count=100),
            ExternalId(movie_id=movie.id, provider="imdb", external_id="tt1234567"),
        ]
    )
    keyword = Keyword(tmdb_id=20, name="friendship")
    session.add(keyword)
    session.flush()
    session.add(MovieKeyword(movie_id=movie.id, keyword_id=keyword.id))
    session.commit()
    ReleaseStatusService(session).classify_batch(100)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(database):
    def override():
        yield database

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_home_discover_search_and_browse(client):
    home = client.get("/api/v1/home").json()
    assert home["trending"][0]["title"] == "Example Film"
    assert home["trending"][0]["rating"] == 8
    assert home["trending"][0]["rating_source"] == "IMDb"
    assert "tmdb_id" not in home["trending"][0]
    assert "display_id" not in home["trending"][0]
    assert home["language_sections"]["ml"]["items"]
    assert home["platforms"][0]["name"] == "Netflix"
    assert (
        client.get(
            "/api/v1/discover?genre=drama&language=ml&director=Example&rating=8&sort=name-asc"
        ).json()["total"]
        == 1
    )
    assert (
        client.get("/api/v1/discover?cinematographer=Example%20Director").json()[
            "items"
        ][0]["title"]
        == "Example Film"
    )
    assert client.get("/api/v1/discover?release_status=released").json()["total"] == 1
    assert client.get("/api/v1/discover?release_status=upcoming").json()["total"] == 1
    search = client.get("/api/v1/search?q=Example").json()
    assert search["movies"]["total"] == 1 and search["people"]["total"] == 2


def test_home_view_more_sections_keep_their_data_context(client, database):
    database.add(
        OttAvailability(
            movie_id=2,
            provider="Prime Video",
            ott_release_date=date.today() + timedelta(days=10),
            status="upcoming",
            confidence=95,
            verification_status="CONFIRMED",
        )
    )
    database.commit()
    latest = client.get("/api/v1/discover?section=latest-theatrical&sort=latest").json()
    upcoming = client.get("/api/v1/discover?section=upcoming-theatrical&sort=oldest").json()
    upcoming_ott = client.get("/api/v1/discover?section=upcoming-ott&sort=ott-release").json()
    recent_ott = client.get("/api/v1/discover?section=recent-ott&sort=ott-release").json()
    assert latest["section"] == "latest-theatrical" and [x["title"] for x in latest["items"]] == ["Example Film"]
    assert upcoming["section"] == "upcoming-theatrical" and [x["title"] for x in upcoming["items"]] == ["Future Film"]
    assert [x["title"] for x in upcoming_ott["items"]] == ["Future Film"]
    assert [x["title"] for x in recent_ott["items"]] == ["Example Film"]
    assert client.get("/api/v1/discover?section=not-a-section").status_code == 422


def test_language_filter_uses_original_language_not_spoken_language(client, database):
    english = Language(iso_639_1="en", english_name="English")
    database.add(english)
    database.query(Movie).filter(Movie.tmdb_id == 101).one().languages.append(english)
    database.commit()

    assert client.get("/api/v1/discover?language=ml").json()["total"] == 2
    assert client.get("/api/v1/discover?language=en").json()["total"] == 0


def test_public_ott_platform_aliases_are_canonical_and_deduplicated(client, database):
    database.add_all(
        [
            OttAvailability(
                movie_id=2,
                provider="Amazon Prime Video",
                status="available",
                verification_status="UNKNOWN",
            ),
            OttAvailability(
                movie_id=2,
                provider="PrimeVideo",
                status="available",
                verification_status="UNKNOWN",
            ),
        ]
    )
    database.commit()

    platforms = client.get("/api/v1/ott").json()["platforms"]
    prime = [item for item in platforms if item["name"] == "Prime Video"]
    assert len(prime) == 1 and prime[0]["movie_count"] == 1
    assert client.get("/api/v1/discover?platform=prime-video").json()["total"] == 1
    assert client.get("/api/v1/ott/prime-video").json()["total"] == 1
    detail = client.get("/api/v1/movies/2/detail").json()["movie"]
    assert [item["provider"] for item in detail["ott"]] == ["Prime Video"]


def test_public_movie_detail_does_not_expose_internal_ott_status(client):
    detail = client.get("/api/v1/movies/1/detail").json()
    for key in (
        "ott_status", "ott_verified", "ott_candidate", "ott_research_status",
        "ott_confidence", "ott_confidence_label",
    ):
        assert key not in detail["movie"]
    for row in detail["movie"]["ott"]:
        assert not {
            "verification_state", "availability_state", "release_state",
            "confidence", "platform_confidence", "date_confidence",
            "last_checked", "last_verified",
        }.intersection(row)


def test_calendar_ott_movie_and_person(client, database):
    database.add(
        OttAvailability(
            movie_id=2,
            provider="RumourTV",
            ott_release_date=site_date(),
            status="announced",
            confidence=30,
        )
    )
    database.commit()
    for period in (
        "previous-week",
        "this-week",
        "next-week",
        "previous-month",
        "this-month",
        "next-month",
    ):
        response = client.get(f"/api/v1/calendar/{period}")
        assert response.status_code == 200
        assert set(response.json()["theatrical"]) == {"items", "total"}
        assert set(response.json()["ott"]) == {"items", "total"}
    calendar = client.get("/api/v1/calendar/this-week").json()
    assert calendar["today"] == site_date().isoformat()
    assert calendar["theatrical"]["items"][0]["certification"] == "U/A"
    assert calendar["ott"]["items"][0]["ott_platform"] == "Netflix"
    assert calendar["ott"]["items"][0]["rating"] == 8
    assert any(item["ott_platform"] == "RumourTV" for item in calendar["ott"]["items"])
    custom_month = client.get(f"/api/v1/calendar/this-month?month={site_date():%Y-%m}")
    assert custom_month.status_code == 200
    assert custom_month.json()["start_date"] == site_date().replace(day=1).isoformat()
    assert client.get("/api/v1/calendar/this-month?month=2026-13").status_code == 422
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
    assert detail["movie"]["theatrical_release_date"] == site_date().isoformat()
    assert detail["movie"]["ott_platform"] == "Netflix"
    assert detail["movie"]["ott_release_date"] == site_date().isoformat()
    assert all(item["provider"].lower() != "tmdb" for item in detail["external_ids"])
    assert detail["external_ids"][0]["url"] == "https://www.imdb.com/title/tt1234567/"
    assert (
        detail["crew_by_role"]["cinematography"][0]["job"] == "Director of Photography"
    )
    assert detail["cast"][0]["profile_path"] == "/actor.jpg"
    assert detail["keywords"] == ["friendship"]
    person = client.get("/api/v1/people/1?credit_type=cast&sort=oldest").json()
    assert "tmdb_id" not in person
    assert person["display_id"] == 10
    assert person["id"] == 1
    assert person["filmography"][0]["character"] == "Hero"
    assert person["filmography"][0]["normalized_role"] == "actor"


def test_calendar_includes_research_date_without_canonical_enrichment(
    client, database
):
    movie = Movie(
        tmdb_id=31150,
        title="Modha Rathiri Calendar Regression",
        original_language="ta",
    )
    database.add(movie)
    database.flush()
    database.add(
        OttEvidence(
            movie_id=movie.id,
            platform="Netflix",
            release_date=date(2027, 6, 21),
            country="IN",
            status="POSSIBLE",
            source_type="established_publication",
            verification_method="CURATED_DATE_LIST",
            confidence=25,
        )
    )
    database.add(
        OttEvidence(
            movie_id=movie.id,
            platform="Netflix",
            release_date=date(2027, 6, 21),
            country=None,
            status="POSSIBLE",
            source_type="another_publication",
            verification_method="CURATED_DATE_LIST",
            confidence=10,
        )
    )
    database.commit()

    payload = client.get("/api/v1/calendar/this-month?month=2027-06").json()
    matches = [item for item in payload["ott"]["items"] if item["id"] == movie.id]

    assert len(matches) == 1
    assert matches[0]["ott_release_date"] == "2027-06-21"
    assert matches[0]["ott_platforms"] == ["Netflix"]
    assert matches[0]["ott_releases"] == [
        {"platform": "Netflix", "release_date": "2027-06-21", "country": "IN"}
    ]


def test_calendar_today_uses_the_configured_site_date(client, monkeypatch):
    canonical_today = date(2026, 8, 29)
    monkeypatch.setattr("app.api.v1.public.site_date", lambda: canonical_today)

    payload = client.get("/api/v1/calendar/this-month").json()

    assert payload["today"] == "2026-08-29"
    assert payload["start_date"] == "2026-08-01"
    assert payload["end_date"] == "2026-08-31"


def test_missing_imdb_rating_is_not_fabricated_and_missing_ott_is_queued(
    client, database
):
    future = client.get("/api/v1/discover?q=Future").json()["items"][0]
    assert future["rating"] is None and future["rating_source"] is None
    assert OttResearchService(database).queue_missing() == 0
    assert database.get(Movie, 2).ott_research_eligibility == "WAITING_RELEASE"
    assert (
        database.query(OttEvidence).filter_by(movie_id=2, status="QUEUED").count() == 0
    )


def test_confirmed_research_publishes_to_canonical_ott_calendar(client, database):
    released = Movie(tmdb_id=103, title="Released Missing OTT", original_language="ml")
    database.add(released)
    database.flush()
    database.add(
        MovieReleaseDate(
            movie_id=released.id,
            country="IN",
            release_date=site_date() - timedelta(days=10),
            release_type="3",
        )
    )
    database.commit()
    assert OttResearchService(database).queue_missing() == 1
    evidence = OttResearchService(database).record_evidence(
        released.id,
        platform="Prime Video",
        release_date=site_date(),
        source_url="https://primevideo.com/detail/example",
        confidence=95,
        source_rank="official_platform",
    )
    assert evidence.status == "CONFIRMED"
    calendar = client.get("/api/v1/calendar/this-week").json()
    assert any(
        item["id"] == released.id and item["ott_platform"] == "Prime Video"
        for item in calendar["ott"]["items"]
    )


def test_request_admin_auth_and_management(client, database, monkeypatch):
    salt = b"0123456789abcdef"
    password = "correct horse"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(
        settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}"
    )
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda _self, movie_id: verified_snapshot(movie_id),
    )
    response = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Missing Film",
            "email": "viewer@example.com",
            "movie_external_id": 999,
        },
    )
    assert response.status_code == 201
    assert "email" not in response.json()
    assert client.get("/api/v1/admin/dashboard").status_code == 401
    assert (
        client.post("/api/v1/admin/login", json={"password": password}).status_code
        == 200
    )
    listing = client.get("/api/v1/admin/requests?search=Missing").json()
    request_id = listing["items"][0]["request_id"]
    assert listing["items"][0]["movie_external_id"] == 999
    assert (
        client.patch(
            f"/api/v1/admin/requests/{request_id}", json={"status": "REVIEWING"}
        ).json()["status"]
        == "REVIEWING"
    )
    assert (
        client.patch(
            f"/api/v1/admin/requests/{request_id}", json={"status": "ADDED"}
        ).status_code
        == 409
    )
    stored = database.query(MovieRequest).filter_by(request_id=request_id).one()
    stored.confirmation_email_last_attempt_at = datetime.now(timezone.utc) - timedelta(
        minutes=6
    )
    database.commit()
    retry = client.post(
        f"/api/v1/admin/requests/{request_id}/emails/confirmation/retry"
    )
    assert retry.status_code == 200 and retry.json()["status"] == "NOT_CONFIGURED"
    health = client.get("/api/v1/admin/data-health")
    assert health.status_code == 200
    assert {
        "movies_total",
        "imdb_id_available",
        "imdb_id_missing",
        "imdb_rating_available",
        "imdb_rating_pending",
        "imdb_provider_quota_or_rate_limited",
        "last_successful_rating_refresh",
    } <= set(health.json()["imdb"])
    assert client.get("/api/v1/admin/images").status_code == 200
    ott_admin = client.get("/api/v1/admin/ott-research")
    assert ott_admin.status_code == 200
    assert {
        "release_status",
        "theatrical_release_date",
        "eligibility",
        "eligibility_label",
    } <= set(ott_admin.json()["items"][0])
    assert (
        ott_admin.json()["daily_usage"]["limit"]
        == settings.OTT_DAILY_RESEARCH_MOVIE_LIMIT
    )
    assert (
        ott_admin.json()["tavily_usage"]["limit"] == settings.TAVILY_MONTHLY_APP_BUDGET
    )
    assert client.get("/api/v1/admin/jobs").status_code == 200
    assert client.get("/api/v1/admin/notifications").status_code == 200


def test_movie_request_external_id_validation_duplicates_and_local_match(
    client, database, monkeypatch
):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda _self, movie_id: verified_snapshot(movie_id),
    )
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.search_movies",
        lambda *_args, **_kwargs: {
            "results": [{"id": 999, "title": "Missing Film", "original_title": "Missing Film"}]
        },
    )
    base = {"movie_name": "Missing Film", "email": "viewer@example.com"}
    name_only = client.post("/api/v1/movie-requests", json=base)
    assert name_only.status_code == 201
    assert name_only.json()["movie_external_id"] == 999
    assert (
        client.post(
            "/api/v1/movie-requests", json=base | {"movie_external_id": 0}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/movie-requests", json=base | {"movie_external_id": "tt1234567"}
        ).status_code
        == 422
    )
    local = client.post(
        "/api/v1/movie-requests", json=base | {"movie_external_id": 101}
    )
    assert local.status_code == 201
    assert local.json()["movie_external_id"] == 101
    stored_local = database.query(MovieRequest).filter_by(external_movie_id=101).one()
    assert stored_local.status == "PENDING"
    assert (
        stored_local.local_movie_id == 1
        and stored_local.movie_existed_at_submission is True
    )
    accepted = client.post(
        "/api/v1/movie-requests", json=base | {"movie_external_id": 998}
    )
    assert accepted.status_code == 201 and accepted.json()["movie_external_id"] == 998
    another_viewer = client.post(
        "/api/v1/movie-requests",
        json={**base, "email": "other@example.com", "movie_external_id": 998},
    )
    assert another_viewer.status_code == 201
    duplicate = client.post(
        "/api/v1/movie-requests", json=base | {"movie_external_id": 998}
    )
    assert duplicate.status_code == 409
    assert (
        duplicate.json()["detail"]
        == "You already have an active request for this movie."
    )


def test_invalid_date_range(client):
    assert (
        client.get(
            "/api/v1/discover?date_from=2026-02-02&date_to=2026-01-01"
        ).status_code
        == 422
    )


def test_contact_requests_are_private_review_items_and_user_ott_reports_are_untrusted(
    client, database, monkeypatch
):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(settings, "DISCORD_BOT_ENDPOINT", "")
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    payload = {
        "request_type": "INCORRECT_OTT",
        "name": "Private Viewer",
        "whatsapp": "+65 8000 0000",
        "phone": "+65 6000 0000",
        "email": "private.viewer@example.test",
        "comment": "The listed platform appears to be incorrect.",
        "movie_name": "Example Film",
        "tmdb_id": 101,
        "expected_ott_platform": "Prime Video",
        "expected_ott_release_date": "2026-09-18",
        "evidence_url": "https://example.test/ott-evidence",
    }
    response = client.post("/api/v1/contact-requests", json=payload)
    assert response.status_code == 201
    assert response.json()["discord_status"] == "NOT_CONFIGURED"
    item = database.query(ContactRequest).filter_by(request_id=response.json()["request_id"]).one()
    assert item.status == "NEW"
    assert item.whatsapp == payload["whatsapp"] and item.phone == payload["phone"]
    evidence = database.get(OttEvidence, item.ott_evidence_id)
    assert evidence.status == "NEEDS_REVIEW"
    assert evidence.verification_method == "USER_REPORT"
    assert evidence.manually_verified is False and evidence.trusted is False

    public_payload = client.get("/api/v1/movies/1/detail").json()
    serialized = str(public_payload)
    assert "Private Viewer" not in serialized
    assert "private.viewer@example.test" not in serialized
    assert "+65 8000 0000" not in serialized
    assert public_payload["movie"].get("ott_candidate") is None

    salt = b"0123456789abcdef"
    password = "contact-admin"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}")
    assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200
    admin_item = client.get(f"/api/v1/admin/contact-requests/{item.request_id}").json()
    assert admin_item["email"] == payload["email"]
    assert admin_item["whatsapp"] == payload["whatsapp"]
    assert admin_item["email_delivery_history"][0]["notification_type"] == "CONTACT_EMAIL_RECEIVED"
    assert admin_item["email_delivery_history"][0]["status"] == "NOT_CONFIGURED"
    retry = client.post(
        f"/api/v1/admin/contact-requests/{item.request_id}/emails/RECEIVED/retry"
    )
    assert retry.status_code == 200 and retry.json()["status"] == "NOT_CONFIGURED"


def test_low_confidence_ott_candidate_is_not_exposed_publicly(client, database):
    evidence = OttEvidence(
        movie_id=1,
        status="NEEDS_REVIEW",
        platform="Prime Video",
        release_date=date(2026, 9, 18),
        source_url="https://example.test/research",
        source_name="Independent report",
        source_type="news",
        source_published_at=date(2026, 9, 1),
        confidence=43,
        platform_confidence=43,
        date_confidence=41,
        summary="A plausible but unverified platform and date claim.",
        verification_method="AUTOMATED_RESEARCH",
        research_run_id="run-low-confidence",
    )
    database.add(evidence)
    database.commit()
    detail = client.get("/api/v1/movies/1/detail").json()["movie"]
    assert "ott_candidate" not in detail
    assert "ott_verified" not in detail
    assert "ott_research_status" not in detail
    assert detail["ott_platform"] == "Netflix"


def test_system_health_marks_missing_automation_heartbeats_stale(database, monkeypatch):
    from app.api.v1.admin import system_health
    from app.workers.celery_app import celery_app

    class RedisHealth:
        def ping(self):
            return True

        def llen(self, _name):
            return 0

    class WorkerHealth:
        def ping(self):
            return {"worker@test": {"ok": "pong"}}

    notices = []
    monkeypatch.setattr("redis.from_url", lambda *_args, **_kwargs: RedisHealth())
    monkeypatch.setattr(celery_app.control, "inspect", lambda **_kwargs: WorkerHealth())
    monkeypatch.setattr(
        "app.api.v1.admin.NotificationService.notify",
        lambda _self, message, *_args, **_kwargs: notices.append(message) or False,
    )
    monkeypatch.setattr(settings, "IMDB_RATING_PROVIDER", "")
    monkeypatch.setattr(settings, "IMDB_RATING_API_URL", "")
    monkeypatch.setattr(settings, "IMDB_RATING_API_KEY", "")

    result = system_health(database, None)
    statuses = {item["name"]: item["status"] for item in result["automations"]}
    assert statuses["MOVIE DISCOVERY"] == "STALE"
    assert statuses["OTT RESEARCH"] == "STALE"
    assert statuses["IMDb REFRESH"] == "NOT_CONFIGURED"
    assert any("MOVIE DISCOVERY STALE" in message for message in notices)


def test_request_verifies_and_persists_authoritative_snapshot(
    client, database, monkeypatch
):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda _self, movie_id: verified_snapshot(movie_id, "L2: Empuraan"),
    )
    response = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Empuraan",
            "email": "viewer@example.com",
            "movie_external_id": 997,
        },
    )
    assert response.status_code == 201
    assert response.json()["verified_title"] == "L2: Empuraan"
    assert response.json()["confirmation_email_status"] == "NOT_CONFIGURED"
    item = database.query(MovieRequest).filter_by(external_movie_id=997).one()
    assert item.movie_name == "Empuraan"
    assert item.verified_title == "L2: Empuraan"
    assert item.original_title == "Verified Original"
    assert item.verified_release_date == date(2026, 1, 2)
    assert item.release_year == 2026
    assert item.verified_original_language == "ml"
    assert item.verified_language_name == "Malayalam"
    assert item.poster_path == "/verified.jpg"
    assert item.imdb_id == "tt7654321"
    assert item.director == "Verified Director"


def test_request_rejects_missing_or_unavailable_live_movie_without_writes(
    client, database, monkeypatch
):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    before = database.query(MovieRequest).count()
    provider_request = httpx.Request("GET", "https://metadata.invalid/movie/996")
    not_found = httpx.HTTPStatusError(
        "not found",
        request=provider_request,
        response=httpx.Response(404, request=provider_request),
    )
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda *_: (_ for _ in ()).throw(not_found),
    )
    missing = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Missing",
            "email": "viewer@example.com",
            "movie_external_id": 996,
        },
    )
    assert missing.status_code == 404
    assert (
        missing.json()["detail"]
        == "Movie could not be found. Please check the ID or use Deep Search."
    )
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda *_: (_ for _ in ()).throw(TimeoutError("secret provider timeout")),
    )
    unavailable = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Missing",
            "email": "viewer@example.com",
            "movie_external_id": 995,
        },
    )
    assert unavailable.status_code == 503
    assert "provider" not in unavailable.json()["detail"].lower()
    assert database.query(MovieRequest).count() == before


@pytest.mark.parametrize("active_status", ["PENDING", "REVIEWING", "FOUND"])
def test_request_duplicate_protection_includes_found(
    client, database, monkeypatch, active_status
):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    database.add(
        MovieRequest(
            request_id=f"REQ-{active_status}",
            movie_name="Verified",
            email="first@example.com",
            external_movie_id=994,
            status=active_status,
        )
    )
    database.commit()
    response = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Again",
            "email": "first@example.com",
            "movie_external_id": 994,
        },
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": "You already have an active request for this movie.",
        "status": active_status,
    }
    assert "email" not in response.text


def test_local_movie_request_is_visible_to_admin_and_not_auto_completed(
    client, database, monkeypatch
):
    salt = b"0123456789abcdef"
    password = "correct horse"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(
        settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}"
    )
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda _self, movie_id: verified_snapshot(movie_id, "Example Film"),
    )

    local = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Example Film",
            "email": "local@example.com",
            "movie_external_id": 101,
        },
    )
    missing = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Missing Film",
            "email": "missing@example.com",
            "movie_external_id": 978,
        },
    )
    assert local.status_code == missing.status_code == 201
    assert (
        database.query(MovieRequest)
        .filter(
            MovieRequest.request_id.in_(
                [local.json()["request_id"], missing.json()["request_id"]]
            )
        )
        .count()
        == 2
    )
    assert MovieRequestAutomationService(database).reconcile()["completed"] == 0
    stored_local = (
        database.query(MovieRequest)
        .filter_by(request_id=local.json()["request_id"])
        .one()
    )
    assert stored_local.status == "PENDING" and stored_local.local_movie_id == 1

    assert (
        client.post("/api/v1/admin/login", json={"password": password}).status_code
        == 200
    )
    listing = client.get("/api/v1/admin/requests").json()["items"]
    visible = {item["request_id"]: item for item in listing}
    assert (
        local.json()["request_id"] in visible
        and missing.json()["request_id"] in visible
    )
    assert visible[local.json()["request_id"]]["movie_existed_at_submission"] is True


def test_request_emails_send_only_after_commit_and_track_admin_delivery(
    client, database, monkeypatch
):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "admin@example.test")
    monkeypatch.setattr(settings, "SITE_URL", "https://tracker.example.test")
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda _self, movie_id: verified_snapshot(movie_id, "Email Film"),
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify",
        lambda *_args, **_kwargs: True,
    )
    messages = []

    def deliver(message):
        assert database.query(MovieRequest).filter_by(external_movie_id=977).one()
        messages.append(message)

    monkeypatch.setattr(MovieRequestEmailService, "_deliver", staticmethod(deliver))
    response = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Email Film",
            "email": "viewer@example.com",
            "movie_external_id": 977,
            "details": "Please review",
        },
    )
    assert response.status_code == 201
    item = (
        database.query(MovieRequest)
        .filter_by(request_id=response.json()["request_id"])
        .one()
    )
    assert (
        item.confirmation_email_status == item.admin_notification_email_status == "SENT"
    )
    assert (
        item.confirmation_email_attempt_count
        == item.admin_notification_email_attempt_count
        == 1
    )
    assert [message["Subject"] for message in messages] == [
        "Movie Request Received — Email Film",
        "New Movie Request — Email Film",
    ]
    assert messages[0].get_body(preferencelist=("plain",)) and messages[0].get_body(
        preferencelist=("html",)
    )
    admin_plain = messages[1].get_body(preferencelist=("plain",)).get_content()
    assert (
        "viewer@example.com" in admin_plain
        and "Please review" in admin_plain
        and "/admin/requests" in admin_plain
    )


def test_smtp_failure_never_rolls_back_saved_request(client, database, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "admin@example.test")
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda _self, movie_id: verified_snapshot(movie_id, "Durable Film"),
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        MovieRequestEmailService,
        "_deliver",
        staticmethod(
            lambda _message: (_ for _ in ()).throw(OSError("temporary SMTP failure"))
        ),
    )

    response = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Durable Film",
            "email": "viewer@example.com",
            "movie_external_id": 976,
        },
    )

    assert response.status_code == 201
    item = (
        database.query(MovieRequest)
        .filter_by(request_id=response.json()["request_id"])
        .one()
    )
    assert item.status == "PENDING"
    assert (
        item.confirmation_email_status
        == item.admin_notification_email_status
        == "FAILED"
    )
    assert "temporary SMTP failure" in item.confirmation_email_last_error


def test_trailer_selection_prefers_official_original_language_and_safe_embed(
    client, database
):
    movie = database.get(Movie, 1)
    selected = TrailerService(database).upsert(
        movie,
        {
            "results": [
                {
                    "site": "YouTube",
                    "key": "EnTrailer01",
                    "type": "Trailer",
                    "name": "Official Trailer",
                    "official": True,
                    "iso_639_1": "en",
                },
                {
                    "site": "YouTube",
                    "key": "Malayalam12",
                    "type": "Trailer",
                    "name": "Fan Trailer",
                    "official": False,
                    "iso_639_1": "ml",
                },
                {
                    "site": "YouTube",
                    "key": "OfficialML1",
                    "type": "Trailer",
                    "name": "Official Malayalam Trailer",
                    "official": True,
                    "iso_639_1": "ml",
                },
                {
                    "site": "YouTube",
                    "key": "not-valid",
                    "type": "Trailer",
                    "name": "Invalid",
                    "official": True,
                },
                {
                    "site": "YouTube",
                    "key": "Reaction1234",
                    "type": "Clip",
                    "name": "Reaction",
                    "official": False,
                },
            ]
        },
        commit=True,
    )
    assert selected.video_key == "OfficialML1"
    payload = client.get("/api/v1/movies/1/detail").json()["trailer"]
    assert payload["video_key"] == "OfficialML1"
    assert payload["embed_url"] == "https://www.youtube-nocookie.com/embed/OfficialML1"
    assert database.query(MovieTrailer).filter_by(video_key="not-valid").count() == 0


def test_missing_or_invalid_trailer_produces_clean_empty_state(client, database):
    database.query(MovieTrailer).delete()
    TrailerService(database).upsert(
        database.get(Movie, 1),
        {"results": [{"site": "Vimeo", "key": "arbitrary", "type": "Trailer"}]},
        commit=True,
    )
    assert client.get("/api/v1/movies/1/detail").json()["trailer"] is None


def test_trailer_videos_payload_ranks_official_original_before_regional_and_drops_junk(client, database):
    movie = database.get(Movie, 1)
    TrailerService(database).upsert(
        movie,
        {
            "results": [
                {
                    "site": "YouTube",
                    "key": "EnTrailer02",
                    "type": "Trailer",
                    "name": "Official Trailer",
                    "official": True,
                    "iso_639_1": "en",
                },
                {
                    "site": "YouTube",
                    "key": "TeaserKey01",
                    "type": "Teaser",
                    "name": "Official Teaser",
                    "official": True,
                    "iso_639_1": "hi",
                },
                {
                    "site": "YouTube",
                    "key": "MlTrailer01",
                    "type": "Trailer",
                    "name": "Official Malayalam Trailer",
                    "official": True,
                    "iso_639_1": "ml",
                    "iso_3166_1": "IN",
                    "size": 720,
                },
                {
                    "site": "YouTube",
                    "key": "ReactVideo1",
                    "type": "Clip",
                    "name": "Reaction",
                    "official": False,
                    "iso_639_1": "ml",
                },
            ]
        },
        commit=True,
    )
    detail = client.get("/api/v1/movies/1/detail").json()
    keys = [item["video_key"] for item in detail["videos"]]
    assert keys[0] == "MlTrailer01"
    assert keys.index("EnTrailer02") < keys.index("TeaserKey01")
    assert "ReactVideo1" not in keys
    assert detail["trailer"]["video_key"] == "MlTrailer01"
    assert detail["videos"][0]["country"] == "IN"
    assert detail["videos"][0]["size"] == 720


def test_trailer_unavailable_demotion_and_recovery(client, database):
    movie = database.get(Movie, 1)
    TrailerService(database).upsert(
        movie,
        {"results": [{"site": "YouTube", "key": "GoneKey1123", "type": "Trailer", "name": "Official Trailer", "official": True, "iso_639_1": "ml"}]},
        commit=True,
    )
    assert database.query(MovieTrailer).filter_by(video_key="GoneKey1123", is_primary=True).one()

    TrailerService(database).upsert(
        database.get(Movie, 1),
        {"results": [{"site": "YouTube", "key": "NewKey88888", "type": "Trailer", "name": "Official Trailer", "official": True, "iso_639_1": "ml"}]},
        commit=True,
    )
    stored = database.query(MovieTrailer).filter_by(video_key="GoneKey1123").one()
    assert stored.is_unavailable is True
    assert database.query(MovieTrailer).filter_by(video_key="NewKey88888", is_primary=True).one()

    TrailerService(database).upsert(
        database.get(Movie, 1),
        {"results": [{"site": "YouTube", "key": "GoneKey1123", "type": "Trailer", "name": "Official Trailer", "official": True, "iso_639_1": "ml"}]},
        commit=True,
    )
    recovered = database.query(MovieTrailer).filter_by(video_key="GoneKey1123").one()
    assert recovered.is_unavailable is False and recovered.last_checked_at is not None


def test_trailer_empty_results_never_marks_stored_videos_unavailable(client, database):
    movie = database.get(Movie, 1)
    TrailerService(database).upsert(
        movie,
        {"results": [{"site": "YouTube", "key": "KeepKey9999", "type": "Trailer", "name": "Official Trailer", "official": True, "iso_639_1": "ml"}]},
        commit=True,
    )
    TrailerService(database).upsert(database.get(Movie, 1), {"results": []}, commit=True)
    assert database.query(MovieTrailer).filter_by(video_key="KeepKey9999", is_unavailable=False).one()


def test_comment_validation_safety_pagination_and_moderation(
    client, database, monkeypatch
):
    monkeypatch.setattr("app.api.v1.public.limit", lambda *_args, **_kwargs: None)
    assert (
        client.post(
            "/api/v1/movies/1/comments", json={"display_name": "", "comment": "Hello"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/movies/1/comments", json={"display_name": "Anand", "comment": ""}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/movies/1/comments",
            json={"display_name": "Anand", "comment": "x" * 2001},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/movies/999/comments",
            json={"display_name": "Anand", "comment": "Hello"},
        ).status_code
        == 404
    )
    response = client.post(
        "/api/v1/movies/1/comments",
        json={
            "display_name": "Anand",
            "email": "private@example.com",
            "comment": "<script>alert(1)</script><b>Great movie</b>",
        },
    )
    assert response.status_code == 201 and response.json()["status"] == "PENDING"
    item = database.get(MovieComment, response.json()["id"])
    assert item.ip_hash and "private@example.com" not in item.ip_hash
    assert client.get("/api/v1/movies/1/comments").json()["total"] == 0

    salt = b"0123456789abcdef"
    password = "correct horse"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(
        settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}"
    )
    assert (
        client.post("/api/v1/admin/login", json={"password": password}).status_code
        == 200
    )
    admin_item = client.get("/api/v1/admin/comments").json()["items"][0]
    assert admin_item["email"] == "private@example.com"
    assert (
        client.patch(
            f"/api/v1/admin/comments/{item.id}", json={"status": "APPROVED"}
        ).status_code
        == 200
    )
    public_item = client.get("/api/v1/movies/1/comments").json()["items"][0]
    assert public_item["comment"] == "<script>alert(1)</script><b>Great movie</b>"
    assert "email" not in public_item
    assert (
        client.patch(
            f"/api/v1/admin/comments/{item.id}", json={"status": "HIDDEN"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/movies/1/comments").json()["total"] == 0
    assert (
        client.patch(
            f"/api/v1/admin/comments/{item.id}", json={"status": "REJECTED"}
        ).status_code
        == 200
    )
    assert client.delete(f"/api/v1/admin/comments/{item.id}").json()["deleted"] is True

    database.add_all(
        [
            MovieComment(
                movie_id=1,
                display_name=f"Viewer {index}",
                comment_text="Comment",
                status="APPROVED",
            )
            for index in range(12)
        ]
    )
    database.commit()
    first = client.get("/api/v1/movies/1/comments?page=1&page_size=10").json()
    second = client.get("/api/v1/movies/1/comments?page=2&page_size=10").json()
    assert (
        first["total"] == 12
        and first["pages"] == 2
        and len(first["items"]) == 10
        and len(second["items"]) == 2
    )


def test_comment_rate_limit_is_scoped_to_movie_and_ip(client, monkeypatch):
    def blocked(_request, bucket, *_args, **_kwargs):
        if bucket == "movie-comment":
            from fastapi import HTTPException

            raise HTTPException(429, "Too many requests; try again later")

    monkeypatch.setattr("app.api.v1.public.limit", blocked)
    response = client.post(
        "/api/v1/movies/1/comments", json={"display_name": "Anand", "comment": "Hello"}
    )
    assert response.status_code == 429


def test_rejected_request_can_be_submitted_again(client, database, monkeypatch):
    monkeypatch.setattr("app.api.v1.operations.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "SMTP_FROM", "")
    database.add(
        MovieRequest(
            request_id="REQ-REJECTED",
            movie_name="Old",
            email="first@example.com",
            external_movie_id=993,
            status="REJECTED",
        )
    )
    database.commit()
    monkeypatch.setattr(
        "app.api.v1.operations.DeepSearchService.verify_movie",
        lambda _self, movie_id: verified_snapshot(movie_id),
    )
    response = client.post(
        "/api/v1/movie-requests",
        json={
            "movie_name": "Again",
            "email": "other@example.com",
            "movie_external_id": 993,
        },
    )
    assert response.status_code == 201
    assert database.query(MovieRequest).filter_by(external_movie_id=993).count() == 2


def test_admin_manual_ott_verification_provenance_and_public_upcoming(
    client, database, monkeypatch
):
    monkeypatch.setattr(
        "app.api.v1.public._queue_on_demand_repair", lambda *_args, **_kwargs: False
    )
    salt = b"0123456789abcdef"
    password = "correct horse"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(
        settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}"
    )
    assert (
        client.post("/api/v1/admin/login", json={"password": password}).status_code
        == 200
    )

    release = date.today() + timedelta(days=4)
    response = client.post(
        "/api/v1/admin/ott-research/movies/2/verify",
        json={
            "platform": "Amazon Prime Video",
            "ott_release_date": release.isoformat(),
            "source_url": "https://primevideo.com/detail/future-film",
            "source_name": "Prime Video",
            "country": "IN",
            "summary": "Official India page explicitly lists the streaming date.",
        },
    )
    assert response.status_code == 200
    canonical = (
        database.query(OttAvailability)
        .filter_by(movie_id=2, provider="Prime Video")
        .one()
    )
    assert canonical.manually_verified is True
    assert canonical.verification_status == "CONFIRMED"
    assert canonical.status == "upcoming"

    detail = client.get("/api/v1/admin/ott-research/movies/2").json()
    assert detail["canonical"]["ott_release_date"] == release.isoformat()
    assert (
        detail["evidence"][0]["source_url"]
        == "https://primevideo.com/detail/future-film"
    )
    assert detail["evidence"][0]["source_published_at"] is None

    public = client.get("/api/v1/movies/2/detail").json()["movie"]
    assert public["ott_platform"] == "Prime Video"
    assert public["ott_release_date"] == release.isoformat()
    assert "ott_status" not in public
    assert "ott_research_status" not in public
    assert client.get("/api/v1/home").json()["upcoming_ott"][0]["id"] == 2
    calendar = client.get(f"/api/v1/calendar/this-month?month={release:%Y-%m}").json()
    assert any(
        item["id"] == 2 and item["ott_platform"] == "Prime Video"
        for item in calendar["ott"]["items"]
    )

    health = client.get("/api/v1/admin/data-health").json()["ott"]
    assert health["movies_with_confirmed_ott_date"] >= 2
    assert "movies_with_platform_but_missing_date" in health
    assert "percentages" in health


def test_confirmation_email_tracking_failure_retry_and_idempotency(
    database, monkeypatch
):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    monkeypatch.setattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")
    item = MovieRequest(
        request_id="REQ-EMAIL",
        movie_name="Verified",
        verified_title="Verified",
        email="viewer@example.com",
        external_movie_id=992,
    )
    database.add(item)
    database.commit()
    attempts = []
    monkeypatch.setattr(
        MovieRequestEmailService,
        "_deliver",
        staticmethod(
            lambda _message: (_ for _ in ()).throw(OSError("SMTP unavailable"))
        ),
    )
    failed = MovieRequestEmailService(database).send(
        item, "confirmation", respect_cooldown=False
    )
    assert failed["status"] == "FAILED"
    assert item.confirmation_email_sent_at is None
    item.confirmation_email_last_attempt_at = datetime.now(timezone.utc) - timedelta(
        minutes=6
    )
    database.commit()
    queued = []
    monkeypatch.setattr(
        "app.workers.celery_app.celery_app.send_task",
        lambda name, args=None, **_kwargs: queued.append((name, args)),
    )
    assert MovieRequestEmailService(database).recover() == ["REQ-EMAIL:confirmation"]
    assert queued == [("notifications.movie_request_email", ["REQ-EMAIL", "confirmation"])]
    monkeypatch.setattr(
        MovieRequestEmailService,
        "_deliver",
        staticmethod(lambda message: attempts.append(message)),
    )
    sent = MovieRequestEmailService(database).send(item, "confirmation")
    assert sent["status"] == "SENT" and item.confirmation_email_sent_at
    assert (
        "aim to make it available within 48 hours"
        in attempts[0].get_body(preferencelist=("plain",)).get_content()
    )
    assert (
        MovieRequestEmailService(database).send(item, "confirmation")["skipped"]
        == "already_sent"
    )
    assert len(attempts) == 1
    history = database.query(RequestCommunication).filter_by(
        movie_request_id=item.id, event_type="CONFIRMATION", channel="email"
    ).one()
    assert history.status == "SENT" and history.attempt_count == 2 and history.sent_at


def test_smtp_authentication_failure_is_recorded_without_credentials(
    database, monkeypatch
):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "private-user")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "private-password")
    item = MovieRequest(
        request_id="REQ-SMTP-AUTH",
        movie_name="Verified",
        verified_title="Verified",
        email="viewer@example.com",
        external_movie_id=989,
    )
    database.add(item)
    database.commit()

    def auth_failure(_message):
        raise smtplib.SMTPAuthenticationError(
            535,
            b"private-user private-password Authorization: Bearer private-token",
        )

    monkeypatch.setattr(
        MovieRequestEmailService, "_deliver", staticmethod(auth_failure)
    )
    result = MovieRequestEmailService(database).send(
        item, "confirmation", respect_cooldown=False
    )

    assert result["status"] == "FAILED"
    assert item.confirmation_email_sent_at is None
    assert "private-user" not in item.confirmation_email_last_error
    assert "private-password" not in item.confirmation_email_last_error
    assert "private-token" not in item.confirmation_email_last_error
    assert "[redacted]" in item.confirmation_email_last_error


def test_reconciliation_marks_added_without_sending_completion(database, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    monkeypatch.setattr(settings, "SITE_URL", "https://movies.example.test")
    request = MovieRequest(
        request_id="REQ-ADD",
        movie_name="Requested",
        verified_title="Requested",
        email="viewer@example.com",
        external_movie_id=991,
        status="FOUND",
    )
    movie = Movie(tmdb_id=991, title="Requested", original_language="ml")
    database.add_all([request, movie])
    database.commit()
    messages = []
    monkeypatch.setattr(
        MovieRequestEmailService,
        "_deliver",
        staticmethod(lambda message: messages.append(message)),
    )
    service = MovieRequestAutomationService(database)
    assert service.reconcile()["completed"] == 1
    assert request.status == "ADDED" and request.local_movie_id == movie.id
    assert request.completion_email_status == "PENDING"
    assert request.completion_email_sent_at is None
    assert service.reconcile()["completed"] == 0
    assert messages == []


def test_admin_manually_notifies_requester_once_and_can_retry_failure(
    client, database, monkeypatch
):
    salt = b"0123456789abcdef"
    password = "notify admin"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(
        settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}"
    )
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    movie = Movie(tmdb_id=992, title="Manual Notify", original_language="ml")
    database.add(movie)
    database.flush()
    item = MovieRequest(
        request_id="REQ-NOTIFY",
        movie_name="Manual Notify",
        verified_title="Manual Notify",
        email="viewer@example.test",
        external_movie_id=movie.tmdb_id,
        local_movie_id=movie.id,
        status="ADDED",
    )
    database.add(item)
    database.commit()

    endpoint = "/api/v1/admin/requests/REQ-NOTIFY/notify-requester"
    assert client.post(endpoint).status_code == 401
    assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200

    monkeypatch.setattr(
        MovieRequestEmailService,
        "_deliver",
        staticmethod(lambda _message: (_ for _ in ()).throw(RuntimeError("SMTP down"))),
    )
    assert client.post(endpoint).status_code == 502
    database.refresh(item)
    assert item.completion_email_sent_at is None
    assert item.completion_email_status == "FAILED"

    messages = []
    monkeypatch.setattr(
        MovieRequestEmailService,
        "_deliver",
        staticmethod(lambda message: messages.append(message)),
    )
    first = client.post(endpoint)
    second = client.post(endpoint)
    assert first.status_code == 200 and first.json()["sent"] is True
    assert second.status_code == 200 and second.json()["sent"] is False
    assert second.json()["skipped"] == "already_sent"
    database.refresh(item)
    assert item.completion_email_sent_at is not None
    assert len(messages) == 1


def test_rejection_email_and_sla_reminders_are_once_only(database, monkeypatch):
    now = datetime.now(timezone.utc)
    rejected = MovieRequest(
        request_id="REQ-NO",
        movie_name="No Movie",
        verified_title="No Movie",
        email="viewer@example.com",
        external_movie_id=990,
        status="REJECTED",
        public_rejection_reason="Rights information is unavailable.",
    )
    database.add(rejected)
    database.commit()
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "requests@example.test")
    messages = []
    monkeypatch.setattr(
        MovieRequestEmailService,
        "_deliver",
        staticmethod(lambda message: messages.append(message)),
    )
    email = MovieRequestEmailService(database)
    assert email.send(rejected, "rejection", respect_cooldown=False)["status"] == "SENT"
    assert email.send(rejected, "rejection")["skipped"] == "already_sent"
    assert (
        "Rights information is unavailable"
        in messages[0].get_body(preferencelist=("plain",)).get_content()
    )

    rows = [
        MovieRequest(
            request_id="REQ-35",
            movie_name="35",
            email="a@example.com",
            external_movie_id=981,
            status="PENDING",
            created_at=now - timedelta(hours=35),
        ),
        MovieRequest(
            request_id="REQ-36",
            movie_name="36",
            email="b@example.com",
            external_movie_id=982,
            status="PENDING",
            created_at=now - timedelta(hours=36, minutes=1),
        ),
        MovieRequest(
            request_id="REQ-47",
            movie_name="47",
            email="c@example.com",
            external_movie_id=983,
            status="REVIEWING",
            created_at=now - timedelta(hours=47),
        ),
        MovieRequest(
            request_id="REQ-48",
            movie_name="48",
            email="d@example.com",
            external_movie_id=984,
            status="FOUND",
            created_at=now - timedelta(hours=48, minutes=1),
        ),
        MovieRequest(
            request_id="REQ-DONE",
            movie_name="Done",
            email="e@example.com",
            external_movie_id=985,
            status="ADDED",
            created_at=now - timedelta(hours=60),
        ),
        MovieRequest(
            request_id="REQ-REJ",
            movie_name="Rejected",
            email="f@example.com",
            external_movie_id=986,
            status="REJECTED",
            created_at=now - timedelta(hours=60),
        ),
    ]
    database.add_all(rows)
    database.commit()
    notices = []
    monkeypatch.setattr(
        "app.services.movie_requests.NotificationService.notify",
        lambda _self, message, *args, **kwargs: notices.append(message) or True,
    )
    service = MovieRequestAutomationService(database)
    result = service.check_sla(now)
    assert result == {"checked": 3, "warnings": 3, "escalations": 1}
    assert rows[0].sla_36_notified_at is None
    assert rows[1].sla_36_notified_at and rows[2].sla_36_notified_at
    assert rows[3].sla_36_notified_at and rows[3].sla_48_notified_at
    assert rows[4].sla_36_notified_at is None and rows[5].sla_48_notified_at is None
    assert service.check_sla(now) == {"checked": 3, "warnings": 0, "escalations": 0}
    assert len(notices) == 4

def test_calendar_groups_multiple_platform_dates_and_cast(client, database):
    db = database
    movie = db.query(Movie).first()
    day = site_date()
    db.add_all([
        OttAvailability(movie_id=movie.id,provider='Lionsgate Play',country='IN',watch_type='subscription',ott_release_date=day,status='released',verification_status='CONFIRMED',confidence=100),
        OttAvailability(movie_id=movie.id,provider='ManoramaMAX',country='IN',watch_type='subscription',ott_release_date=day,status='released',verification_status='CONFIRMED',confidence=100),
    ])
    db.commit()
    payload=client.get('/api/v1/calendar/this-week').json()['ott']['items']
    matches=[x for x in payload if x['id']==movie.id]
    assert len(matches)==1
    assert {'Lionsgate Play','ManoramaMAX'} <= set(matches[0]['ott_platforms'])
    assert len(matches[0]['ott_releases']) >= 2
    db.close()
