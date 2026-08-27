from datetime import datetime, timezone

from app.models.movie_metadata import MovieRating
from app.models.operations import OperationState
from app.services.rating_provider import IMDbRatingRefreshService, OmdbRatingProvider, RatingResult


class FakeProvider:
    source = "IMDb"

    def fetch(self, imdb_id):
        assert imdb_id == "tt1234567"
        return RatingResult(8.4, 123456, imdb_id, datetime.now(timezone.utc))


def test_rating_refresh_reuses_movie_rating_and_persistent_state(database):
    # The shared fixture has no external ID, so attach an approved provider lookup key.
    from app.models.movie_metadata import ExternalId
    database.add(ExternalId(movie_id=1, provider="imdb", external_id="tt1234567"))
    database.commit()
    result = IMDbRatingRefreshService(database, FakeProvider()).refresh(batch_size=1)
    rating = database.query(MovieRating).filter_by(movie_id=1).one()
    state = database.query(OperationState).filter_by(name="imdb_rating_refresh").one()
    assert result["processed"] == result["updated"] == 1
    assert rating.source == "IMDb" and rating.rating == 8.4 and rating.vote_count == 123456
    assert rating.last_updated_at is not None and state.cursor == 1 and state.processed_count == 1


def test_omdb_provider_parses_imdb_fields_without_scraping(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"Response": "True", "imdbRating": "7.9", "imdbVotes": "12,345"}

    monkeypatch.setattr("app.services.rating_provider.httpx.get", lambda *args, **kwargs: Response())
    result = OmdbRatingProvider("https://ratings.example.test/", "secret").fetch("tt1234567")
    assert result.rating == 7.9 and result.vote_count == 12345 and result.source_id == "tt1234567"


def test_invalid_imdb_id_is_not_queried():
    assert OmdbRatingProvider("https://ratings.example.test/", "secret").fetch("123") is None
