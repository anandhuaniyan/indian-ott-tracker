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


@pytest.fixture()
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
        OttAvailability(movie_id=first.id, provider="Netflix", status="confirmed", confidence=95),
    ])
    session.commit()
    try: yield session
    finally: session.close(); engine.dispose()
