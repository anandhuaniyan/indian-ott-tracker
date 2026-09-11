from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.genre import Genre
from app.models.language import Language
from app.models.movie import Movie
from app.models.movie_metadata import MovieCredit, MovieReleaseDate, Person
from app.models.ott_availability import OttAvailability
from app.config.settings import settings


def _trigram_set(text) -> set[str]:
    """Build the pg_trgm-style '' + word + ' ' padded trigram set."""
    normalized = "  " + "  ".join(str(text).strip().lower().split())
    return {normalized[i : i + 3] for i in range(len(normalized) - 2)}


def _trigram_similarity(left, right) -> float:
    """Jaccard over 3-grams, matching Postgres pg_trgm.similarity semantics."""
    a, b = _trigram_set(left), _trigram_set(right)
    if not a or not b:
        return 1.0 if a == b else 0.0
    return len(a & b) / len(a | b)


def register_sqlite_trigram_similarity(engine) -> None:
    """Mirror Postgres pg_trgm similarity() as an SQLite function for tests."""
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _register(dbapi_connection, _connection_record):
        dbapi_connection.create_function(
            "similarity",
            2,
            lambda left, right: _trigram_similarity(left, right),
        )


@pytest.fixture(autouse=True)
def isolate_external_rate_limit_store(monkeypatch):
    """Keep endpoint tests from consuming the live Redis rate-limit buckets.

    Dedicated rate-limit tests replace this stub with their own deterministic
    Redis fake, so the limiter itself remains covered.
    """
    from app.core.rate_limit import _reset_local_fallback_for_tests
    from app.core.session_auth import _reset_session_store_for_tests

    _reset_local_fallback_for_tests()
    _reset_session_store_for_tests()
    monkeypatch.setattr(
        "app.core.rate_limit.redis.from_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError()),
    )
    # A developer's production .env correctly enables Secure cookies, while the
    # in-process TestClient uses HTTP. Keep authentication tests isolated from
    # that host-level deployment setting.
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    yield
    _reset_local_fallback_for_tests()
    _reset_session_store_for_tests()


@pytest.fixture()
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    register_sqlite_trigram_similarity(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    genre = Genre(tmdb_id=18, name="Drama", slug="drama")
    language = Language(iso_639_1="ml", english_name="Malayalam")
    actor = Person(tmdb_id=10, name="Example Actor", known_for_department="Acting")
    director = Person(tmdb_id=11, name="Example Director", known_for_department="Directing")
    first = Movie(tmdb_id=101, title="Example Film", release_date=date.today(), original_language="ml", genres=[genre], languages=[language])
    second = Movie(tmdb_id=102, title="Future Film", release_date=date.today() + timedelta(days=20), original_language="ml", genres=[genre], languages=[language])
    session.add_all([first, second, actor, director]); session.flush()
    session.add_all([
        MovieCredit(movie_id=first.id, person_id=actor.id, credit_type="cast", character="Hero"),
        MovieCredit(movie_id=first.id, person_id=director.id, credit_type="crew", department="Directing", job="Director"),
        MovieReleaseDate(movie_id=first.id, country="IN", release_date=date.today() - timedelta(days=10), release_type="3"),
        MovieReleaseDate(movie_id=second.id, country="IN", release_date=date.today() + timedelta(days=20), release_type="3"),
        OttAvailability(movie_id=first.id, provider="Netflix", status="available", confidence=95, verification_status="UNKNOWN"),
    ])
    session.commit()
    try: yield session
    finally: session.close(); engine.dispose()
