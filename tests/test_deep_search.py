"""Focused offline coverage for live TMDB Deep Search and explicit imports."""

import hashlib

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.database.connection import get_db
from app.main import app
from app.models.movie import Movie
from app.services.deep_search import DeepSearchService


class MemoryCache:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, _ttl, value):
        self.values[key] = value


class FakeTMDB:
    def __init__(self):
        self.calls = []

    def get(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if endpoint == "/search/movie":
            return {
                "page": 1, "total_pages": 1, "total_results": 2,
                "results": [
                    {"id": 101, "title": "Example Film", "original_title": "Original Example", "release_date": "2026-01-02", "original_language": "ml", "overview": "Movie", "poster_path": "/one.jpg", "popularity": 10},
                    {"id": 999, "title": "English Film", "release_date": "2026-01-03", "original_language": "en"},
                ],
            }
        if endpoint == "/search/person":
            return {"page": 1, "total_pages": 1, "total_results": 1, "results": [{"id": 10, "name": "Example Actor", "known_for_department": "Acting", "profile_path": "/actor.jpg", "known_for": [{"id": 101, "media_type": "movie", "title": "Example Film"}]}]}
        if endpoint == "/find/tt1234567":
            return {"movie_results": [{"id": 101, "title": "Example Film"}], "person_results": []}
        if endpoint == "/movie/101/images":
            assert params["include_image_language"] == "en,ml,null"
            return {"posters": [{"file_path": "/poster.jpg", "iso_639_1": "ml"}], "backdrops": [{"file_path": "/backdrop.jpg", "iso_639_1": None}], "logos": [{"file_path": "/logo.png", "iso_639_1": "en"}]}
        if endpoint == "/movie/101":
            return {
                "id": 101, "title": "Example Film", "original_title": "Original Example", "original_language": "ml", "release_date": "2026-01-02", "overview": "Movie", "vote_average": 7.9, "vote_count": 123,
                "credits": {"cast": [{"id": 10, "name": "Example Actor", "character": "Hero", "profile_path": "/actor.jpg"}], "crew": [{"id": 11, "name": "Example Director", "job": "Director", "department": "Directing"}, {"id": 12, "name": "Camera Person", "job": "Director of Photography", "department": "Camera"}]},
                "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [{"release_date": "2026-02-01T00:00:00Z", "type": 3}]}, {"iso_3166_1": "IN", "release_dates": [{"release_date": "2026-01-02T00:00:00Z", "type": 3, "certification": "U/A"}]}]},
                "external_ids": {"imdb_id": "tt1234567", "wikidata_id": "Q1"},
                "keywords": {"keywords": [{"id": 4, "name": "friendship"}]},
                "alternative_titles": {"titles": [{"iso_3166_1": "IN", "title": "Example Alternate"}]},
                "recommendations": {"results": [{"id": 201, "title": "Recommended"}]},
                "similar": {"results": [{"id": 202, "title": "Similar"}]},
                "watch/providers": {"results": {"IN": {"link": "https://tmdb.example/watch", "flatrate": [{"provider_id": 8, "provider_name": "Provider", "logo_path": "/provider.jpg"}]}}},
            }
        if endpoint == "/person/10":
            return {
                "id": 10, "name": "Example Actor", "profile_path": "/actor.jpg", "known_for_department": "Acting", "biography": "Biography",
                "external_ids": {"imdb_id": "nm123"}, "images": {"profiles": [{"file_path": "/actor2.jpg"}]},
                "movie_credits": {"cast": [{"id": 101, "title": "Example Film", "character": "Hero", "release_date": "2026-01-02"}], "crew": [{"id": 201, "title": "Directed Film", "job": "Director", "release_date": "2025-01-01"}]},
            }
        raise AssertionError(f"Unexpected TMDB endpoint {endpoint}")


def test_movie_people_find_detail_and_cache(database, monkeypatch):
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured-for-test")
    provider = FakeTMDB()
    service = DeepSearchService(database, client=provider, cache=MemoryCache())

    search = service.search_movies("Example", year=2026, language="ml")
    assert search["results"][0]["id"] == 101
    assert search["results"][0]["original_language_name"] == "Malayalam"
    assert search["results"][0]["local_movie_id"] == 1
    assert len(search["results"]) == 1
    assert provider.calls[-1][1]["year"] == 2026
    service.search_movies("Example", year=2026, language="ml")
    assert len([call for call in provider.calls if call[0] == "/search/movie"]) == 1

    people = service.search_people("Example")
    assert people["results"][0]["local_person_id"] == 1
    assert people["results"][0]["known_for"][0]["title"] == "Example Film"
    assert service.find_imdb("tt1234567")["movies"][0]["in_library"] is True

    detail = service.movie_detail(101)
    assert detail["source"] == "live"
    assert detail["movie"]["id"] == 101 and detail["movie"]["local_movie_id"] == 1
    assert detail["cast"][0]["character"] == "Hero"
    assert detail["crew"]["Cinematography"][0]["job"] == "Director of Photography"
    assert detail["releases"][0]["country"] == "IN"
    assert detail["releases"][0]["releases"][0]["certification"] == "U/A"
    assert detail["images"]["logos"][0]["file_path"] == "/logo.png"
    assert detail["external_ids"]["imdb_id"] == "tt1234567"
    assert detail["keywords"][0]["name"] == "friendship"
    assert detail["recommendations"][0]["id"] == 201
    assert detail["similar"][0]["id"] == 202
    assert detail["watch_providers"]["items"][0]["name"] == "Provider"

    person = service.person_detail(10)
    assert person["person"]["id"] == 10 and person["person"]["local_person_id"] == 1
    assert person["credits"]["Acting"][0]["character"] == "Hero"
    assert person["credits"]["Directing"][0]["job"] == "Director"
    assert person["profiles"][0]["file_path"] == "/actor2.jpg"


def test_invalid_imdb_id_and_rate_limit_are_handled(database, monkeypatch):
    def override():
        yield database
    app.dependency_overrides[get_db] = override
    try:
        client = TestClient(app)
        assert client.get("/api/v1/deep-search/find?external_id=not-an-imdb-id").status_code == 422
        monkeypatch.setattr("app.api.v1.deep_search.limit", lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(429, "Too many requests")))
        response = client.get("/api/v1/deep-search/movies?q=Example")
        assert response.status_code == 429
    finally:
        app.dependency_overrides.clear()


def test_admin_import_is_authenticated_and_duplicate_safe(database, monkeypatch):
    def override():
        yield database
    app.dependency_overrides[get_db] = override
    salt = b"deep-search-test"
    password = "correct horse"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}")
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured-for-test")
    monkeypatch.setattr("app.api.v1.admin.limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.api.v1.admin.TMDbMovieService.get_movie", lambda _self, movie_id: {"id": movie_id, "title": "Imported Film", "original_language": "ml", "release_date": "2026-08-01", "poster_path": "/imported.jpg"})

    class Task:
        id = "task-123"
    sent = []
    monkeypatch.setattr("app.workers.celery_app.celery_app.send_task", lambda name, args=None: sent.append((name, args)) or Task())
    try:
        anonymous = TestClient(app)
        assert anonymous.post("/api/v1/admin/deep-search/movies/500/import").status_code == 401
        client = TestClient(app)
        assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200
        imported = client.post("/api/v1/admin/deep-search/movies/500/import").json()
        assert imported["created"] is True and imported["queued"] is True
        assert imported["display_id"] == 500
        assert database.query(Movie).filter_by(tmdb_id=500).count() == 1
        duplicate = client.post("/api/v1/admin/deep-search/movies/500/import").json()
        assert duplicate["status"] == "already_exists" and duplicate["queued"] is False
        repaired = client.post("/api/v1/admin/deep-search/movies/500/repair").json()
        assert repaired["status"] == "repair_queued"
        assert sent == [("repair.movie", [imported["local_movie_id"]]), ("repair.movie", [imported["local_movie_id"]])]
    finally:
        app.dependency_overrides.clear()
