import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app.config.settings import settings
from app.services.tmdb.client import TMDbClient, TMDbRequestError, metadata_request_context


def client_for(monkeypatch, responses):
    monkeypatch.setattr(settings, "TMDB_API_KEY", "private-test-key")
    monkeypatch.setattr(settings, "TMDB_ACCESS_TOKEN", "")
    sleeps, calls = [], []
    monkeypatch.setattr("app.services.tmdb.client.time.sleep", sleeps.append)
    monkeypatch.setattr("app.services.tmdb.client.random.uniform", lambda *_: 0)
    client = TMDbClient(request_delay=0)
    client.client.close()

    def handler(request):
        calls.append(request)
        value = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(value, Exception):
            raise value
        status, headers = value if isinstance(value, tuple) else (value, {})
        return httpx.Response(status, headers=headers, json={"id": 1423253})

    client.client = httpx.Client(base_url=client.BASE_URL, transport=httpx.MockTransport(handler))
    return client, calls, sleeps


@pytest.mark.parametrize("failure", [500, 502, 503, 504, httpx.ReadTimeout("private-test-key"),
                                      httpx.ConnectError("DNS failed"), httpx.ReadError("connection reset")])
def test_transient_then_success(monkeypatch, failure, caplog):
    client, calls, sleeps = client_for(monkeypatch, [failure, 200])
    with caplog.at_level("INFO"), metadata_request_context(6321, 1423253):
        assert client.get("/movie/1423253")["id"] == 1423253
    assert len(calls) == 2
    assert sleeps == [5]
    assert "movie_id=6321" in caplog.text
    assert "outcome=success" in caplog.text
    assert "private-test-key" not in caplog.text


def test_success_immediately(monkeypatch):
    client, calls, sleeps = client_for(monkeypatch, [200])
    assert client.get("/movie/1423253")["id"] == 1423253
    assert len(calls) == 1 and not sleeps


@pytest.mark.parametrize("status", [400, 401, 403, 404, 501])
def test_non_transient_no_retry(monkeypatch, status):
    client, calls, sleeps = client_for(monkeypatch, [status])
    with metadata_request_context(6321, 1423253), pytest.raises(TMDbRequestError) as error:
        client.get("/movie/1423253")
    assert error.value.status_code == status
    assert not error.value.retryable
    assert len(calls) == 1 and not sleeps


def test_exhaustion_is_bounded(monkeypatch):
    client, calls, sleeps = client_for(monkeypatch, [502])
    with metadata_request_context(6321, 1423253), pytest.raises(TMDbRequestError) as error:
        client.get("/movie/1423253")
    assert len(calls) == error.value.attempts == 5
    assert sleeps == [5, 10, 20, 40]
    assert error.value.retryable


@pytest.mark.parametrize("header", ["27", "http-date"])
def test_retry_after(monkeypatch, header):
    if header == "http-date":
        header = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=45))
    client, calls, sleeps = client_for(monkeypatch, [(429, {"Retry-After": header}), 200])
    client.get("/movie/1423253")
    assert len(calls) == 2
    assert sleeps[0] >= 26


def test_long_retry_after_defers_without_sleep(monkeypatch):
    client, calls, sleeps = client_for(monkeypatch, [(429, {"Retry-After": "3600"})])
    with pytest.raises(TMDbRequestError) as error:
        client.get("/movie/1423253")
    assert error.value.retry_after == 3600
    assert len(calls) == 1 and not sleeps


@pytest.mark.parametrize("status,kind", [(502, "TMDB_METADATA_RETRY_PENDING"),
                                        (404, "TMDB_NOT_FOUND"), (401, "TMDB_METADATA_NEEDS_REVIEW")])
def test_batch_continues_and_records_failure(database, monkeypatch, status, kind):
    from app.workers import tasks
    from app.models.movie import Movie
    from app.models.operations import DataQualityIssue, OperationState
    movies = database.query(Movie).order_by(Movie.id).all()
    first, second = movies[:2]
    calls = []

    def enrich(self, movie):
        calls.append(movie.id)
        if movie.id == first.id:
            raise TMDbRequestError(f"/movie/{movie.tmdb_id}", status, attempts=5 if status == 502 else 1)

    monkeypatch.setattr(tasks, "_run", lambda run: run(database))
    monkeypatch.setattr("app.services.movie_metadata_service.MovieMetadataService.enrich_movie", enrich)
    monkeypatch.setattr(tasks.ReleaseStatusService, "classify_movie", lambda *_: None)
    monkeypatch.setattr(tasks.OttResearchService, "queue_movie", lambda *_: None)
    result = tasks.metadata_enrichment.run()
    assert calls == [first.id, second.id]
    assert result["processed"] == 1 and result["failed"] == 1
    assert result["cursor"] == second.id
    issue = database.query(DataQualityIssue).filter_by(movie_id=first.id, issue_type=kind).one()
    assert issue.resolved_at is None
    assert not tasks.metadata_enrichment.autoretry_for

    if status == 502:
        # The pending failure becomes eligible in a later scheduled run and
        # clears on recovery, even though the cursor has advanced past it.
        detail = json.loads(issue.detail)
        detail["next_retry_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        issue.detail = json.dumps(detail)
        database.commit()
        monkeypatch.setattr("app.services.movie_metadata_service.MovieMetadataService.enrich_movie", lambda *_: None)
        recovered = tasks.metadata_enrichment.run()
        assert recovered["processed"] == 1
        assert issue.resolved_at is not None
