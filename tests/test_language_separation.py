"""Canonical original-language separation regression coverage."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database.connection import get_db
from app.main import app
from app.models.movie import Movie
from app.models.movie_metadata import MovieReleaseDate
from app.models.genre import Genre
from app.models.ott_availability import OttAvailability
from app.services.language_health import (
    language_health,
    normalize_stored_movie_languages,
)
from app.services.languages import (
    LANGUAGE_BY_CODE,
    language_filter_codes,
    normalize_language_code,
)
from app.services.movie_metadata_service import MovieMetadataService
from app.services.tmdb.movie_service import TMDbMovieService


@pytest.fixture()
def client(database):
    def override():
        yield database

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("eng", "en"),
        ("JPN", "ja"),
        (" kor ", "ko"),
        ("fra", "fr"),
        ("fre", "fr"),
        ("spa", "es"),
        ("zh", "zh"),
        ("cn", "cn"),
        ("yue", "yue"),
        ("cmn", "cmn"),
        (None, None),
        (" ", None),
    ],
)
def test_language_aliases_are_conservative(raw, expected):
    assert normalize_language_code(raw) == expected


def test_primary_language_configuration_is_complete_and_canonical():
    expected = {
        "ml": ("Malayalam", "malayalam", "indian"),
        "ta": ("Tamil", "tamil", "indian"),
        "te": ("Telugu", "telugu", "indian"),
        "hi": ("Hindi", "hindi", "indian"),
        "kn": ("Kannada", "kannada", "indian"),
        "en": ("English", "english", "international"),
        "ko": ("Korean", "korean", "international"),
        "ja": ("Japanese", "japanese", "international"),
        "zh": ("Chinese", "chinese", "international"),
        "es": ("Spanish", "spanish", "international"),
        "fr": ("French", "french", "international"),
    }
    assert {
        code: (
            LANGUAGE_BY_CODE[code].display_name,
            LANGUAGE_BY_CODE[code].slug,
            LANGUAGE_BY_CODE[code].group,
        )
        for code in expected
    } == expected
    assert all(LANGUAGE_BY_CODE[code].enabled for code in expected)
    assert language_filter_codes("all") is None
    assert language_filter_codes("indian") == ("ml", "ta", "te", "hi", "kn")
    assert language_filter_codes("jpn") == ("ja",)


def test_language_health_and_normalization_change_only_language(database):
    alias = Movie(
        tmdb_id=92001,
        title="Alias Language",
        original_title="Untouched title",
        original_language=" ENG ",
        release_date=date(2026, 1, 2),
        poster_path="/poster.jpg",
    )
    unsupported = Movie(
        tmdb_id=92002,
        title="Unsupported Language",
        original_language="vi",
    )
    unknown = Movie(
        tmdb_id=92003,
        title="Unknown Language",
        original_language="xx",
    )
    null = Movie(tmdb_id=92004, title="Null Language", original_language=None)
    database.add_all([alias, unsupported, unknown, null])
    database.commit()

    before = language_health(database)
    english = next(
        item for item in before["by_language"] if item["code"] == "en"
    )
    assert english == {
        "code": "en",
        "language": "English",
        "total": 1,
        "correct": 0,
        "mismatch": 1,
        "unknown": 0,
    }
    assert before["null_original_language_count"] == 1
    assert before["unsupported_language_count"] == 2
    assert before["unknown_language_count"] == 1
    assert before["language_mismatch_count"] == 1

    result = normalize_stored_movie_languages(database, commit=True)
    database.refresh(alias)
    assert result["updated_movies"] == 1
    assert alias.original_language == "en"
    assert alias.title == "Alias Language"
    assert alias.original_title == "Untouched title"
    assert alias.release_date == date(2026, 1, 2)
    assert alias.poster_path == "/poster.jpg"
    assert result["after"]["language_mismatch_count"] == 0


def test_language_api_search_and_discover_have_no_cross_language_leakage(
    client, database
):
    movies = [
        Movie(
            tmdb_id=92100 + index,
            title=f"Shared Search {code}",
            original_language=code,
            release_date=date(2026, 5, 1),
        )
        for index, code in enumerate(("en", "ja", "ko", "zh", "es", "fr", "ta"))
    ]
    database.add_all(movies)
    database.commit()

    for code in ("en", "ja", "ko", "zh", "es", "fr", "ta"):
        discover = client.get(
            f"/api/v1/discover?language={code}&year=2026&sort=name-asc"
        ).json()
        assert discover["total"] == 1
        assert {item["language"] for item in discover["items"]} == {code}
        search = client.get(
            f"/api/v1/search?q=Shared%20Search&language={code}"
        ).json()
        assert search["movies"]["total"] == 1
        assert {item["language"] for item in search["movies"]["items"]} == {code}

    assert client.get("/api/v1/discover?language=eng&year=2026").json()[
        "items"
    ][0]["language"] == "en"
    international = client.get(
        "/api/v1/discover?language_region=international&year=2026"
    ).json()
    assert {item["language"] for item in international["items"]} == {
        "en", "ja", "ko", "zh", "es", "fr"
    }
    indian = client.get("/api/v1/discover?language_region=indian&year=2026").json()
    indian_codes = {item["language"] for item in indian["items"]}
    assert "ta" in indian_codes
    assert indian_codes <= {"ml", "ta", "te", "hi", "kn", "bn", "mr", "pa", "gu", "or", "as"}


def test_calendar_language_filter_is_server_side(client, database):
    japanese = Movie(
        tmdb_id=92201,
        title="Calendar Japanese",
        original_language="ja",
    )
    korean = Movie(
        tmdb_id=92202,
        title="Calendar Korean",
        original_language="ko",
    )
    database.add_all([japanese, korean])
    database.flush()
    database.add_all(
        [
            MovieReleaseDate(
                movie_id=japanese.id,
                country="IN",
                release_date=date.today(),
                release_type="3",
            ),
            MovieReleaseDate(
                movie_id=korean.id,
                country="IN",
                release_date=date.today(),
                release_type="3",
            ),
        ]
    )
    database.commit()

    response = client.get("/api/v1/calendar/this-week?language=jpn")
    assert response.status_code == 200
    assert response.json()["language"] == "ja"
    assert {
        item["language"] for item in response.json()["theatrical"]["items"]
    } == {"ja"}
    assert response.json()["languages"]


def test_global_language_filters_compose_without_cross_language_leakage(
    client, database
):
    action = Genre(tmdb_id=28, name="Action", slug="action")
    movies = {}
    for index, code in enumerate(("ml", "ta", "te", "hi", "kn", "en", "ko", "ja", "zh", "es", "fr")):
        movie = Movie(
            tmdb_id=92300 + index,
            title=f"Global Filter {code}",
            original_language=code,
            release_date=date(2026, 9, 12),
            genres=[action] if code == "fr" else [],
        )
        database.add(movie)
        movies[code] = movie
    database.flush()
    database.add_all([
        OttAvailability(
            movie_id=movies["ja"].id,
            provider="Netflix",
            status="upcoming",
            verification_status="CONFIRMED",
            confidence=95,
            country="IN",
            ott_release_date=date.today() + timedelta(days=5),
        ),
        OttAvailability(
            movie_id=movies["ko"].id,
            provider="Netflix",
            status="upcoming",
            verification_status="CONFIRMED",
            confidence=95,
            country="IN",
            ott_release_date=date.today() + timedelta(days=6),
        ),
        OttAvailability(
            movie_id=movies["ml"].id,
            provider="Prime Video",
            status="released",
            verification_status="CONFIRMED",
            confidence=95,
            country="IN",
            ott_release_date=date.today(),
        ),
        MovieReleaseDate(
            movie_id=movies["en"].id,
            country="IN",
            release_date=date(2026, 9, 12),
            release_type="3",
        ),
    ])
    database.commit()

    all_result = client.get("/api/v1/discover?q=Global%20Filter&language=all").json()
    assert {item["language"] for item in all_result["items"]} == {
        "ml", "ta", "te", "hi", "kn", "en", "ko", "ja", "zh", "es", "fr"
    }
    indian = client.get("/api/v1/discover?q=Global%20Filter&language=indian").json()
    assert {item["language"] for item in indian["items"]} == {
        "ml", "ta", "te", "hi", "kn"
    }
    netflix_japanese = client.get("/api/v1/ott/netflix?language=ja").json()
    assert {item["language"] for item in netflix_japanese["items"]} == {"ja"}
    prime_indian = client.get(
        "/api/v1/ott/prime-video?language=indian"
    ).json()
    assert {item["language"] for item in prime_indian["items"]} == {"ml"}
    calendar_english = client.get(
        "/api/v1/calendar/this-month?month=2026-09&language=en"
    ).json()
    assert {item["language"] for item in calendar_english["theatrical"]["items"]} == {"en"}
    upcoming_korean = client.get(
        "/api/v1/discover?section=upcoming-ott&language=ko"
    ).json()
    assert {item["language"] for item in upcoming_korean["items"]} == {"ko"}
    action_french = client.get(
        "/api/v1/discover?genre=action&language=fr"
    ).json()
    assert {item["language"] for item in action_french["items"]} == {"fr"}
    search_japanese = client.get(
        "/api/v1/search?q=Global%20Filter&language=ja"
    ).json()
    assert {item["language"] for item in search_japanese["movies"]["items"]} == {"ja"}


def test_tmdb_ingestion_normalizes_original_language(database):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    MovieMetadataService(database)._update_movie_scalars(
        movie, {"original_language": "JPN"}
    )
    assert movie.original_language == "ja"

    class Client:
        def get(self, path, **params):
            return params

    service = TMDbMovieService()
    service.client = Client()
    params = service.discover_movies_by_language_and_year("eng", 2026)
    assert params["with_original_language"] == "en"
