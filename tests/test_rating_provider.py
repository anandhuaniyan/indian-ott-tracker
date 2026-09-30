from datetime import date, datetime, timedelta, timezone
import logging

import httpx
import pytest

from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieRating
from app.models.operations import BackfillRecord, MovieRequest, OperationState
from app.services.backfill import IMDbIdRecoveryService
from app.services.rating_provider import (
    IMDbRatingRefreshService,
    OmdbKeyStateStore,
    OmdbRatingProvider,
    ProviderInvalidKey,
    ProviderMalformedResponse,
    ProviderQuotaExhausted,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    RATING_AVAILABLE,
    RATING_BLOCKED_BY_QUOTA,
    RATING_INVALID_ID,
    RATING_NEEDS_REVIEW,
    RATING_NOT_YET_RATED,
    RATING_TEMPORARY_FAILURE,
    RatingResult,
    _parse_omdb_keys,
    _reset_omdb_key_state_for_tests,
    omdb_key_fingerprint,
)
from app.services.tmdb.client import TMDbRequestError


class FakeProvider:
    source = "IMDb"

    def fetch(self, imdb_id):
        assert imdb_id == "tt1234567"
        return RatingResult(8.4, 123456, imdb_id, datetime.now(timezone.utc))


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.setex_calls = []

    def exists(self, key):
        return key in self.values

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.setex_calls.append((key, ttl, value))
        return True


class FailingRedis:
    def exists(self, _key):
        raise ConnectionError("redis unavailable")

    def setex(self, _key, _ttl, _value):
        raise ConnectionError("redis unavailable")


class OmdbResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@pytest.fixture(autouse=True)
def reset_omdb_key_state():
    _reset_omdb_key_state_for_tests()
    yield
    _reset_omdb_key_state_for_tests()


def make_pool(keys, redis_client=None):
    return OmdbRatingProvider(
        "https://ratings.example.test/",
        keys[0],
        key_pool=keys[1:],
        state_store=OmdbKeyStateStore(redis_client=redis_client or MemoryRedis()),
    )


@pytest.mark.parametrize("exhausted_count", [0, 1, 2, 4])
def test_omdb_pool_uses_first_available_key_in_order(monkeypatch, exhausted_count):
    keys = [f"test-key-{index}" for index in range(1, 6)]
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params["apikey"])
        if len(calls) <= exhausted_count:
            return OmdbResponse({"Response": "False", "Error": "Request limit reached!"})
        return OmdbResponse({"Response": "True", "imdbRating": "8.1", "imdbVotes": "1,234"})

    monkeypatch.setattr("app.services.rating_provider.httpx.get", fake_get)
    result = make_pool(keys).fetch("tt1234567")

    assert result.rating == 8.1
    assert calls == keys[: exhausted_count + 1]


def test_omdb_pool_all_five_exhausted_returns_typed_error(monkeypatch):
    keys = [f"quota-key-{index}" for index in range(1, 6)]
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params["apikey"])
        return OmdbResponse({"Response": "False", "Error": "Request limit reached!"})

    monkeypatch.setattr("app.services.rating_provider.httpx.get", fake_get)
    with pytest.raises(ProviderQuotaExhausted) as caught:
        make_pool(keys).fetch("tt1234567")

    assert calls == keys
    assert all(key not in str(caught.value) for key in keys)


def test_omdb_not_found_does_not_rotate(monkeypatch):
    keys = ["not-found-primary", "must-not-be-used"]
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params["apikey"])
        return OmdbResponse({"Response": "False", "Error": "Movie not found!"})

    monkeypatch.setattr("app.services.rating_provider.httpx.get", fake_get)
    result = make_pool(keys).fetch("tt1234567")

    assert result.status == "NOT_FOUND"
    assert calls == [keys[0]]


def test_omdb_invalid_key_is_skipped_for_next_valid_key(monkeypatch):
    keys = ["invalid-primary", "valid-secondary"]
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params["apikey"])
        if params["apikey"] == keys[0]:
            return OmdbResponse({"Response": "False", "Error": "Invalid API key!"})
        return OmdbResponse({"Response": "True", "imdbRating": "7.3", "imdbVotes": "400"})

    monkeypatch.setattr("app.services.rating_provider.httpx.get", fake_get)
    provider = make_pool(keys)
    assert provider.fetch("tt1234567").rating == 7.3
    calls.clear()
    assert provider.fetch("tt1234567").rating == 7.3
    assert calls == [keys[1]]


def test_omdb_key_parser_trims_deduplicates_and_ignores_empty(monkeypatch):
    monkeypatch.setattr(settings, "OMDB_API_KEYS", " first, ,second,first, second ,third,")
    monkeypatch.setattr(settings, "IMDB_RATING_API_KEY", "legacy-must-not-append")
    assert _parse_omdb_keys() == ["first", "second", "third"]


def test_omdb_legacy_key_is_compatibility_fallback_only(monkeypatch):
    monkeypatch.setattr(settings, "OMDB_API_KEYS", " , ")
    monkeypatch.setattr(settings, "IMDB_RATING_API_KEY", " legacy-key ")
    assert _parse_omdb_keys() == ["legacy-key"]


def test_omdb_logs_and_exceptions_never_contain_raw_keys(monkeypatch, caplog):
    keys = ["raw-secret-one", "raw-secret-two"]
    monkeypatch.setattr(
        "app.services.rating_provider.httpx.get",
        lambda *_args, **_kwargs: OmdbResponse({"Response": "False", "Error": "Request limit reached!"}),
    )
    with caplog.at_level(logging.INFO), pytest.raises(ProviderQuotaExhausted) as caught:
        make_pool(keys).fetch("tt1234567")

    output = caplog.text + str(caught.value)
    assert all(key not in output for key in keys)
    assert all(omdb_key_fingerprint(key) in caplog.text for key in keys)


def test_omdb_redis_quota_marker_contains_fingerprint_only(monkeypatch):
    raw_key = "marker-secret-key"
    cache = MemoryRedis()
    monkeypatch.setattr(
        "app.services.rating_provider.httpx.get",
        lambda *_args, **_kwargs: OmdbResponse({"Response": "False", "Error": "Request limit reached!"}),
    )
    with pytest.raises(ProviderQuotaExhausted):
        make_pool([raw_key], cache).fetch("tt1234567")

    marker, ttl, value = cache.setex_calls[0]
    assert marker == f"omdb:key:{omdb_key_fingerprint(raw_key)}:quota_exhausted"
    assert raw_key not in marker and ttl > 0 and value == "1"


def test_omdb_redis_unavailable_uses_process_fallback(monkeypatch):
    keys = ["fallback-primary", "fallback-secondary"]
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params["apikey"])
        if params["apikey"] == keys[0]:
            return OmdbResponse({"Response": "False", "Error": "Request limit reached!"})
        return OmdbResponse({"Response": "True", "imdbRating": "6.8", "imdbVotes": "10"})

    monkeypatch.setattr("app.services.rating_provider.httpx.get", fake_get)
    provider = make_pool(keys, FailingRedis())
    assert provider.fetch("tt1234567").rating == 6.8
    calls.clear()
    assert provider.fetch("tt1234567").rating == 6.8
    assert calls == [keys[1]]


def test_omdb_timeout_does_not_consume_other_credentials(monkeypatch):
    keys = ["timeout-primary", "untouched-secondary", "untouched-third"]
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params["apikey"])
        raise httpx.ReadTimeout("request URL contains a secret")

    monkeypatch.setattr("app.services.rating_provider.httpx.get", fake_get)
    with pytest.raises(ProviderTimeout) as caught:
        make_pool(keys).fetch("tt1234567")

    assert calls == [keys[0]]
    assert all(key not in str(caught.value) for key in keys)


def test_omdb_shared_redis_marker_coordinates_provider_instances(monkeypatch):
    keys = ["shared-primary", "shared-secondary"]
    cache = MemoryRedis()
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params["apikey"])
        if params["apikey"] == keys[0]:
            return OmdbResponse({"Response": "False", "Error": "Request limit reached!"})
        return OmdbResponse({"Response": "True", "imdbRating": "7.0", "imdbVotes": "20"})

    monkeypatch.setattr("app.services.rating_provider.httpx.get", fake_get)
    assert make_pool(keys, cache).fetch("tt1234567").rating == 7.0
    calls.clear()
    assert make_pool(keys, cache).fetch("tt1234567").rating == 7.0
    assert calls == [keys[1]]


def test_omdb_rate_limit_fails_over_sequentially(monkeypatch):
    keys = ["limited-primary", "available-secondary"]
    calls = []

    def fake_get(_url, *, params, **_kwargs):
        calls.append(params["apikey"])
        if params["apikey"] == keys[0]:
            return OmdbResponse({"Response": "False", "Error": "Too many requests"})
        return OmdbResponse({"Response": "True", "imdbRating": "7.6", "imdbVotes": "30"})

    monkeypatch.setattr("app.services.rating_provider.httpx.get", fake_get)
    assert make_pool(keys).fetch("tt1234567").rating == 7.6
    assert calls == keys


def test_omdb_malformed_and_unknown_errors_are_typed_without_rotation(monkeypatch):
    keys = ["malformed-primary", "untouched-secondary"]
    calls = []

    def malformed_get(_url, *, params, **_kwargs):
        calls.append(params["apikey"])
        return OmdbResponse(["unexpected"])

    monkeypatch.setattr("app.services.rating_provider.httpx.get", malformed_get)
    with pytest.raises(ProviderMalformedResponse):
        make_pool(keys).fetch("tt1234567")
    assert calls == [keys[0]]

    calls.clear()
    monkeypatch.setattr(
        "app.services.rating_provider.httpx.get",
        lambda _url, *, params, **_kwargs: (
            calls.append(params["apikey"]) or OmdbResponse({"Response": "False", "Error": "Unexpected provider fault"})
        ),
    )
    with pytest.raises(ProviderUnavailable):
        make_pool(keys).fetch("tt1234567")
    assert calls == [keys[0]]


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


def test_quota_failure_never_discards_a_verified_rating(database):
    database.add(ExternalId(movie_id=1, provider="imdb", external_id="tt1234567"))
    database.add(MovieRating(
        movie_id=1,
        source="IMDb",
        rating=8.4,
        vote_count=4544,
        status=RATING_AVAILABLE,
    ))
    database.commit()

    class QuotaProvider:
        def fetch(self, _): raise ProviderQuotaExhausted("quota reached")

    result = IMDbRatingRefreshService(database, QuotaProvider()).refresh_movie(1)
    record = database.query(MovieRating).filter_by(movie_id=1, source="IMDb").one()

    assert result["status"] == RATING_AVAILABLE
    assert record.rating == 8.4 and record.vote_count == 4544
    assert record.status == RATING_AVAILABLE
    assert "quota" in (record.last_error or "").lower()


def test_na_result_never_nulls_an_existing_rating(database):
    database.add(ExternalId(movie_id=1, provider="imdb", external_id="tt1234567"))
    database.add(MovieRating(
        movie_id=1,
        source="IMDb",
        rating=6.7,
        status=RATING_AVAILABLE,
    ))
    database.commit()

    class NaProvider:
        def fetch(self, _): return RatingResult(None, None, "tt1234567", datetime.now(timezone.utc), RATING_NOT_YET_RATED)

    result = IMDbRatingRefreshService(database, NaProvider()).refresh_movie(1)
    record = database.query(MovieRating).filter_by(movie_id=1, source="IMDb").one()

    assert result["status"] == RATING_AVAILABLE
    assert record.rating == 6.7
    assert record.status == RATING_AVAILABLE
    assert "preserved" in (record.last_error or "")


def test_needs_review_state_is_not_resolved_by_provider(database):
    database.add(ExternalId(movie_id=1, provider="imdb", external_id="tt1234567"))
    database.add(MovieRating(
        movie_id=1,
        source="IMDb",
        status=RATING_NEEDS_REVIEW,
    ))
    database.commit()

    class ResolvingProvider:
        def fetch(self, _): return RatingResult(9.1, 1200, "tt1234567", datetime.now(timezone.utc))

    result = IMDbRatingRefreshService(database, ResolvingProvider()).refresh_movie(1)
    record = database.query(MovieRating).filter_by(movie_id=1, source="IMDb").one()

    assert result["status"] == RATING_NEEDS_REVIEW
    assert record.rating is None
    assert record.status == RATING_NEEDS_REVIEW
