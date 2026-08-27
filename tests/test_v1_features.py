"""Offline integration coverage for the movie-only V1 public and admin flows."""

from datetime import date, datetime, timedelta, timezone
import hashlib

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
from app.models.operations import MovieRequest, OperationState
from app.models.ott_availability import OttAvailability


@pytest.fixture()
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    today = date.today()
    genre = Genre(tmdb_id=18, name="Drama", slug="drama")
    language = Language(iso_639_1="ml", english_name="Malayalam", native_name="മലയാളം")
    actor = Person(tmdb_id=10, name="Example Actor", known_for_department="Acting")
    director = Person(tmdb_id=11, name="Example Director", known_for_department="Directing")
    movie = Movie(tmdb_id=101, title="Example Film", original_title="Original Example", overview="A real stored overview.", release_date=today, poster_path="/poster.jpg", backdrop_path="/backdrop.jpg", popularity=90, vote_average=8.2, vote_count=120, original_language="ml", status="Released", tagline="A stored tagline", runtime_minutes=130, genres=[genre], languages=[language])
    future = Movie(tmdb_id=102, title="Future Film", release_date=today + timedelta(days=20), popularity=30, vote_average=7, original_language="ml", genres=[genre], languages=[language])
    session.add_all([movie, future, actor, director]); session.flush()
    session.add_all([
        MovieCredit(movie_id=movie.id, person_id=actor.id, credit_type="cast", character="Hero", cast_order=0),
        MovieCredit(movie_id=movie.id, person_id=director.id, credit_type="crew", department="Directing", job="Director"),
        MovieCredit(movie_id=movie.id, person_id=director.id, credit_type="crew", department="Camera", job="Director of Photography"),
        OttAvailability(movie_id=movie.id, provider="Netflix", provider_logo="/netflix.png", ott_release_date=today, status="confirmed", source_type="official", source_url="https://netflix.com/title/example", confidence=95),
        MovieReleaseDate(movie_id=movie.id, country="IN", release_date=today, release_type="theatrical", certification="U/A"),
        MovieImage(movie_id=movie.id, image_type="logo", source="tmdb", source_id="logo", original_url="https://image.tmdb.org/logo.png"),
        MovieRating(movie_id=movie.id, source="imdb", rating=8, vote_count=100),
        ExternalId(movie_id=movie.id, provider="imdb", external_id="tt123"),
    ])
    keyword = Keyword(tmdb_id=20, name="friendship"); session.add(keyword); session.flush(); session.add(MovieKeyword(movie_id=movie.id, keyword_id=keyword.id))
    session.commit()
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
    assert home["language_sections"]["ml"]["items"]
    assert home["platforms"][0]["name"] == "Netflix"
    assert client.get("/api/v1/discover?genre=drama&language=ml&director=Example&rating=8&sort=name-asc").json()["total"] == 1
    assert client.get("/api/v1/discover?cinematographer=Example%20Director").json()["items"][0]["title"] == "Example Film"
    search = client.get("/api/v1/search?q=Example").json()
    assert search["movies"]["total"] == 1 and search["people"]["total"] == 2


def test_calendar_ott_movie_and_person(client):
    assert client.get("/api/v1/calendar/this-week").status_code == 200
    assert client.get("/api/v1/calendar/not-real").status_code == 404
    assert client.get("/api/v1/ott").json()["confirmed"][0]["title"] == "Example Film"
    platform = client.get("/api/v1/ott/netflix").json()
    assert platform["total"] == 1 and platform["platform"] == "Netflix"
    detail = client.get("/api/v1/movies/1/detail").json()
    assert detail["movie"]["certification"] == "U/A"
    assert detail["crew_by_role"]["cinematography"][0]["job"] == "Director of Photography"
    assert detail["keywords"] == ["friendship"]
    person = client.get("/api/v1/people/1?credit_type=cast&sort=oldest").json()
    assert person["filmography"][0]["character"] == "Hero"


def test_request_admin_auth_and_management(client, database, monkeypatch):
    salt = b"0123456789abcdef"; password = "correct horse"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}")
    monkeypatch.setattr("app.services.notification_service.NotificationService.notify", lambda *args, **kwargs: True)
    response = client.post("/api/v1/movie-requests", json={"movie_name": "Missing Film", "email": "viewer@example.com"})
    assert response.status_code == 201
    assert client.get("/api/v1/admin/dashboard").status_code == 401
    assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200
    listing = client.get("/api/v1/admin/requests?search=Missing").json()
    request_id = listing["items"][0]["request_id"]
    assert client.patch(f"/api/v1/admin/requests/{request_id}", json={"status": "REVIEWING"}).json()["status"] == "REVIEWING"
    assert client.get("/api/v1/admin/data-health").status_code == 200
    assert client.get("/api/v1/admin/images").status_code == 200
    assert client.get("/api/v1/admin/ott-research").status_code == 200
    assert client.get("/api/v1/admin/jobs").status_code == 200
    assert client.get("/api/v1/admin/notifications").status_code == 200


def test_invalid_date_range(client):
    assert client.get("/api/v1/discover?date_from=2026-02-02&date_to=2026-01-01").status_code == 422
