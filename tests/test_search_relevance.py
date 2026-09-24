"""Regression coverage for the precision local search (/api/v1/search).

The original implementation OR'd title / original-title / alternative-title /
credits(people) / keyword matches and ordered purely by popularity, so a
"vaazha" query surfaced movies that merely shared a cast member ("Vaazhai
Janaki") or a loose alternative-title word.  These tests lock in the
title-centric deterministic ranking: exact > prefix > substring > gated
trigram fuzzy, with LIMIT as a maximum instead of a fill target.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.models.movie import Movie
from app.models.movie_metadata import (
    AlternativeTitle,
    Keyword,
    MovieCredit,
    MovieKeyword,
    Person,
)
from conftest import register_sqlite_trigram_similarity


def _movie(title, popularity, release_days=0):
    return Movie(
        tmdb_id=abs(hash(title)) % 1000000,
        title=title,
        release_date=date.today() + timedelta(days=release_days),
        original_language="ml",
        status="Released",
        popularity=popularity,
    )


@pytest.fixture()
def database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    register_sqlite_trigram_similarity(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    vaazha = _movie("Vaazha", 90)
    vaazha_ii = _movie("Vaazha II", 40)
    vaazhai = _movie("Vaazhai", 30)
    sessions = _movie("Sessions", 85)
    aavesham = _movie("Aavesham", 20)
    aavesha = _movie("Aavesha", 15)
    vedalam = _movie("Vedalam", 50)
    premam = _movie("Premam", 10)
    premalu = _movie("Premalu", 45)
    drishyam = _movie("Drishyam", 35)
    drishyam_two = _movie("Drishyam 2", 25)
    spider_man = _movie("Spider-Man: Into the Spider-Verse", 100)
    spider_man_noir = _movie("Spider-Man: No Way Home", 90)
    dark_knight = _movie("The Dark Knight", 80)
    commandovin = _movie("Commandovin Love Story", 5)
    chuttalabbai = _movie("Chuttalabbai", 4)
    alternate = _movie("Alternate Veethi", 50)
    leaky = _movie("Leaky Film", 60)
    ninety_six = _movie("96", 70)
    lionheart = _movie("Lionheart", 1)
    # Multi-word precision test movies — "another woman" case
    another_woman = _movie("Another Woman", 50)
    another_world = _movie("Another World", 40)
    another_way = _movie("Another Way", 30)
    another_me = _movie("Another Me", 20)
    another_end = _movie("Another End", 10)
    woman = _movie("Woman", 60)
    a_woman = _movie("A Woman", 55)
    another_father = _movie("Another Father", 15)
    vaazh = _movie("Vaazh", 45)
    fillers = [_movie(f"Filler {i:02d}", i) for i in range(1, 41)]

    session.add_all(
        [
            vaazha,
            vaazha_ii,
            vaazhai,
            sessions,
            aavesham,
            aavesha,
            vedalam,
            premam,
            premalu,
            drishyam,
            drishyam_two,
            spider_man,
            spider_man_noir,
            dark_knight,
            commandovin,
            chuttalabbai,
            alternate,
            leaky,
            ninety_six,
            lionheart,
            another_woman,
            another_world,
            another_way,
            another_me,
            another_end,
            woman,
            a_woman,
            another_father,
            vaazh,
            *fillers,
        ]
    )
    janaki = Person(
        tmdb_id=51199, name="Vaazhai Janaki", known_for_department="Acting"
    )
    session.add_all([janaki, Person(tmdb_id=1, name="Random Someone")])
    session.flush()

    session.add_all(
        [
            MovieCredit(
                movie_id=commandovin.id,
                person_id=janaki.id,
                credit_type="cast",
                character="Hero",
            ),
            AlternativeTitle(
                movie_id=chuttalabbai.id,
                title="Unakkaaga Vaazha Ninaikkiren",
                country="IN",
            ),
            AlternativeTitle(
                movie_id=chuttalabbai.id,
                title="Unakkaga Vazha Ninaikkiren",
                country="IN",
            ),
            AlternativeTitle(movie_id=alternate.id, title="Vaazha", country="IN"),
            AlternativeTitle(movie_id=vedalam.id, title="Avesham", country="IN"),
        ]
    )
    keyword = Keyword(tmdb_id=999, name="vaazha")
    session.add(keyword)
    session.flush()
    for movie in (leaky, commandovin):
        session.add(MovieKeyword(movie_id=movie.id, keyword_id=keyword.id))
    session.commit()
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


def _movie_titles(client, q):
    return [
        item["title"]
        for item in client.get(f"/api/v1/search?q={q}").json()["movies"]["items"]
    ]


def test_vaazha_excludes_credit_and_alternative_word_matches(client):
    result = client.get("/api/v1/search?q=vaazha").json()
    titles = [item["title"] for item in result["movies"]["items"]]
    assert titles[0] == "Vaazha"
    assert {
        "Vaazha",
        "Vaazha II",
        "Vaazhai",
        "Alternate Veethi",  # matches only via its exact alternative title
    } == set(titles)
    assert "Commandovin Love Story" not in titles  # cast member no longer leaks
    assert "Chuttalabbai" not in titles  # loose alt-title word no longer leaks
    assert "Leaky Film" not in titles  # keyword no longer leaks
    assert result["people"]["total"] == 1
    assert result["people"]["items"][0]["name"] == "Vaazhai Janaki"


def test_fuzzy_typo_tolerance_still_finds_the_movie(client):
    for typo in ("vazha", "vaaza"):
        titles = _movie_titles(client, typo)
        assert "Vaazha" in titles
        assert "Commandovin Love Story" not in titles
        assert "Chuttalabbai" not in titles
        assert "Leaky Film" not in titles
    assert _movie_titles(client, "vazha")[0] == "Vaazha"  # no word-inside noise
    titles = _movie_titles(client, "aavesam")
    assert "Aavesham" in titles


def test_exact_and_prefix_rankings(client):
    titles = _movie_titles(client, "vaazh")
    assert titles[0] == "Vaazh"
    assert "Vaazha" in titles and "Vaazha II" in titles and "Vaazhai" in titles
    assert client.get("/api/v1/search?q=premam").json()["movies"]["total"] == 1


def test_short_queries_prefer_exact_and_prefix_without_substring(client):
    titles = _movie_titles(client, "96")
    assert titles == ["96"]
    titles = _movie_titles(client, "a")
    # Query "a" matches all movie titles starting with "a" via prefix.
    assert "Aavesham" in titles
    assert "Aavesha" in titles
    assert "Alternate Veethi" in titles
    assert "A Woman" in titles
    # Movies that don't start with "a" must not leak via substring matching.
    assert "Lionheart" not in titles
    assert "Leaky Film" not in titles


def test_unrelated_query_returns_nothing(client):
    result = client.get("/api/v1/search?q=xylophone").json()
    assert result["movies"]["total"] == 0
    assert result["movies"]["items"] == []
    assert result["people"]["total"] == 0


def test_blank_and_whitespace_query_returns_empty(client):
    for q in ("", "   "):
        result = client.get(f"/api/v1/search?q={q}").json()
        assert result["movies"]["total"] == 0 and result["people"]["total"] == 0


def test_query_is_normalized(client):
    titles = _movie_titles(client, "%20%20Vaazha%20%20")
    assert titles[0] == "Vaazha"


def test_exact_query_suppresses_fuzzy_only_candidates(client):
    result = client.get("/api/v1/search?q=aavesham").json()
    titles = [item["title"] for item in result["movies"]["items"]]
    assert titles[0] == "Aavesham"
    assert "Aavesha" not in titles  # fuzzy-only, suppressed because exact match exists


def test_fuzzy_typo_recovers_when_no_exact_match(client):
    titles = _movie_titles(client, "aaveshm")
    assert "Aavesham" in titles
    assert "Aavesha" in titles  # both are fuzzy for this query, no strong match