from datetime import date, datetime, timedelta, timezone

from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieRating
from app.models.operations import BackfillRecord, MovieRequest, OperationState
from app.services.backfill import IMDbIdRecoveryService
from app.services.rating_provider import (
    IMDbRatingRefreshService,
    OmdbRatingProvider,
    ProviderQuotaExhausted,
    ProviderRateLimited,
    ProviderUnavailable,
    RATING_AVAILABLE,
    RATING_BLOCKED_BY_QUOTA,
    RATING_INVALID_ID,
    RATING_NOT_YET_RATED,
    RATING_TEMPORARY_FAILURE,
    RatingResult,
)
from app.services.tmdb.client import TMDbRequestError


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


def test_movie_without_imdb_id_never_calls_rating_provider(database):
    class NeverCalled:
        def fetch(self, _):
            raise AssertionError("a movie without an IMDb ID must not be queried")

    result = IMDbRatingRefreshService(database, NeverCalled()).refresh(batch_size=10)

    assert result["processed"] == 0
    assert result["complete"] is True


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


def test_na_rating_remains_due_for_a_later_refresh(database, monkeypatch):
    database.add(ExternalId(movie_id=1, provider="imdb", external_id="tt1234567"))
    database.commit()

    class Response:
        status_code = 200
        def json(self): return {"Response": "True", "imdbRating": "N/A", "imdbVotes": "N/A"}

    monkeypatch.setattr("app.services.rating_provider.httpx.get", lambda *args, **kwargs: Response())
    provider = OmdbRatingProvider("https://ratings.example.test/", "secret")
    first = IMDbRatingRefreshService(database, provider).refresh(batch_size=1)
    record = database.query(MovieRating).filter_by(movie_id=1, source="IMDb").one()
    assert first["processed"] == 1 and first["updated"] == 0
    assert record.rating is None and record.status == RATING_NOT_YET_RATED
    assert record.next_check_at > record.last_attempt_at

    # It is deferred, not permanently marked successful.
    assert IMDbRatingRefreshService(database, FakeProvider()).refresh(batch_size=1)["processed"] == 0
    record.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    database.commit()
    assert IMDbRatingRefreshService(database, FakeProvider()).refresh(batch_size=1)["updated"] == 1
    assert record.rating == 8.4 and record.status == RATING_AVAILABLE


def test_quota_and_provider_failure_pause_without_hammering(database):
    database.add(ExternalId(movie_id=1, provider="imdb", external_id="tt1234567"))
    database.commit()

    class QuotaProvider:
        def fetch(self, _): raise ProviderQuotaExhausted("quota reached key=secret")

    result = IMDbRatingRefreshService(database, QuotaProvider()).refresh(batch_size=10)
    record = database.query(MovieRating).filter_by(movie_id=1, source="IMDb").one()
    state = database.query(OperationState).filter_by(name="imdb_rating_refresh").one()
    assert result["stopped"] == "quota_or_rate_limit"
    assert record.status == RATING_BLOCKED_BY_QUOTA and state.status == "BLOCKED"
    assert "secret" not in (record.last_error or "")

    record.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    database.commit()

    class OfflineProvider:
        def fetch(self, _): raise ProviderUnavailable("provider unavailable")

    result = IMDbRatingRefreshService(database, OfflineProvider()).refresh(batch_size=10)
    assert result["stopped"] == "provider_unavailable"
    assert record.status == RATING_TEMPORARY_FAILURE


def test_rate_limited_checkpoint_resumes_when_record_is_due(database):
    database.add(ExternalId(movie_id=1, provider="imdb", external_id="tt1234567"))
    database.commit()

    class RateLimitedOnce:
        def fetch(self, _):
            raise ProviderRateLimited("daily rate limit reached")

    stopped = IMDbRatingRefreshService(database, RateLimitedOnce()).refresh(batch_size=10)
    record = database.query(MovieRating).filter_by(movie_id=1, source="IMDb").one()
    state = database.query(OperationState).filter_by(name="imdb_rating_refresh").one()
    assert stopped["stopped"] == "quota_or_rate_limit"
    assert state.status == "BLOCKED" and record.status == RATING_BLOCKED_BY_QUOTA

    record.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    database.commit()
    resumed = IMDbRatingRefreshService(database, FakeProvider()).refresh(batch_size=10)

    assert resumed["updated"] == 1
    assert record.rating == 8.4 and record.status == RATING_AVAILABLE
    assert state.status == "IDLE"


def test_active_requested_movie_is_prioritized_before_remaining_old_catalog(database):
    old = Movie(
        tmdb_id=9101,
        title="Old Catalog",
        release_date=date.today() - timedelta(days=3000),
        popularity=40,
    )
    requested = Movie(
        tmdb_id=9102,
        title="Requested Catalog",
        release_date=date.today() - timedelta(days=3000),
        popularity=1,
    )
    database.add_all([old, requested])
    database.flush()
    database.add_all([
        ExternalId(movie_id=old.id, provider="imdb", external_id="tt9101000"),
        ExternalId(movie_id=requested.id, provider="imdb", external_id="tt9102000"),
        MovieRequest(
            request_id="REQ-RATING-PRIORITY",
            movie_name=requested.title,
            email="viewer@example.com",
            external_movie_id=requested.tmdb_id,
            status="REVIEWING",
        ),
    ])
    database.commit()
    calls = []

    class RecordingProvider:
        def fetch(self, imdb_id):
            calls.append(imdb_id)
            return RatingResult(7.2, 100, imdb_id, datetime.now(timezone.utc))

    IMDbRatingRefreshService(database, RecordingProvider()).refresh(batch_size=1)

    assert calls == ["tt9102000"]


def test_invalid_id_is_recorded_without_calling_provider(database):
    database.add(ExternalId(movie_id=1, provider="imdb", external_id="bad-id"))
    database.commit()

    class NeverCalled:
        def fetch(self, _): raise AssertionError("provider must not be called")

    result = IMDbRatingRefreshService(database, NeverCalled()).refresh(batch_size=1)
    record = database.query(MovieRating).filter_by(movie_id=1, source="IMDb").one()
    assert result["processed"] == 1 and record.status == RATING_INVALID_ID
    assert record.next_check_at is None


def test_available_rating_is_not_overwritten_before_due(database):
    database.add(ExternalId(movie_id=1, provider="imdb", external_id="tt1234567"))
    database.add(MovieRating(
        movie_id=1,
        source="IMDb",
        rating=7.5,
        status=RATING_AVAILABLE,
        last_attempt_at=datetime.now(timezone.utc),
        last_updated_at=datetime.now(timezone.utc),
        next_check_at=datetime.now(timezone.utc) + timedelta(days=30),
    ))
    database.commit()

    class NeverCalled:
        def fetch(self, _): raise AssertionError("fresh rating must not be overwritten")

    assert IMDbRatingRefreshService(database, NeverCalled()).refresh(batch_size=10)["processed"] == 0
    assert database.query(MovieRating).filter_by(movie_id=1).one().rating == 7.5


def test_imdb_id_recovery_creates_rating_eligibility(database, monkeypatch):
    database.add_all([
        ExternalId(movie_id=1, provider="imdb", external_id="tt0000001"),
        ExternalId(movie_id=2, provider="imdb", external_id="tt0000002"),
    ])
    movie = Movie(tmdb_id=9001, title="Recovered ID", release_date=date.today(), popularity=100)
    database.add(movie); database.commit()
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured-for-test")
    monkeypatch.setattr(
        "app.services.backfill.TMDbMovieService.get_movie_external_ids",
        lambda _self, movie_id: {"id": movie_id, "imdb_id": "tt7654321"},
    )

    result = IMDbIdRecoveryService(database).run(batch_size=1)
    external = database.query(ExternalId).filter_by(movie_id=movie.id, provider="imdb").one()
    pending = database.query(MovieRating).filter_by(movie_id=movie.id, source="IMDb").one()
    assert result["recovered"] == 1 and external.external_id == "tt7654321"
    assert pending.status == "PENDING" and pending.rating is None


def test_permanent_metadata_404_preserves_movie_and_stops_retries(database, monkeypatch):
    database.add_all([
        ExternalId(movie_id=1, provider="imdb", external_id="tt0000001"),
        ExternalId(movie_id=2, provider="imdb", external_id="tt0000002"),
    ])
    movie = Movie(tmdb_id=9002, title="Preserved Local Movie", release_date=date.today())
    database.add(movie); database.commit()
    monkeypatch.setattr(settings, "TMDB_API_KEY", "configured-for-test")

    def missing(_self, _movie_id):
        raise TMDbRequestError("/movie/9002/external_ids", 404)

    monkeypatch.setattr("app.services.backfill.TMDbMovieService.get_movie_external_ids", missing)
    result = IMDbIdRecoveryService(database).run(batch_size=1)
    record = database.query(BackfillRecord).filter_by(
        operation="ratings.imdb_id_backfill", entity_type="movie", entity_id=movie.id
    ).one()
    assert result["permanent"] == 1 and record.status == "PERMANENT"
    assert database.get(Movie, movie.id).title == "Preserved Local Movie"
    assert IMDbIdRecoveryService(database).run(batch_size=1)["processed"] == 0
