"""Regression coverage for auditable manual/scheduled research controls."""

import hashlib
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.database.connection import get_db
from app.main import app
from app.models.operations import MovieRequest
from app.models.research import ResearchRun
from app.services.notification_service import NotificationService
from app.services.rating_provider import IMDbRatingRefreshService
from app.services.research import ResearchPipelineService
from app.workers.celery_app import notify_task_failure


def _admin_client(database, monkeypatch):
    def override_db():
        yield database

    salt = b"0123456789abcdef"
    password = "research admin password"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000).hex()
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", f"pbkdf2_sha256$1000${salt.hex()}${digest}")
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    assert client.post("/api/v1/admin/login", json={"password": password}).status_code == 200
    return client


def test_manual_research_records_provenance_results_and_prevents_concurrency(database, monkeypatch):
    service = ResearchPipelineService(database)
    run, created = service.create_run(movie_id=1, trigger_type="ADMIN_MANUAL", initiated_by="admin", category="FULL")
    assert created is True
    active, created_again = service.create_run(movie_id=1, trigger_type="AUTOMATED_SCHEDULE", initiated_by="celery:beat", category="OTT")
    assert created_again is False and active.run_id == run.run_id

    monkeypatch.setattr("app.services.research.MovieMetadataService.enrich_movie", lambda self, movie: movie)
    monkeypatch.setattr("app.services.research.IMDbRatingRefreshService.refresh_movie", lambda self, movie_id: {"status": "NOT_CONFIGURED", "configured": False})
    monkeypatch.setattr("app.services.research.OTTIntelligenceService.refresh_movie", lambda self, movie_id, research_run_id=None: {"movie_id": movie_id, "providers": {"TMDB": {"status": "HEALTHY"}}, "evidence": 0})
    monkeypatch.setattr("app.services.research.WebOttResearchService.research_movie", lambda self, movie_id, research_run_id=None: {"movie_id": movie_id, "provider": "FakeSearch", "configured": True, "queries": ["Example Film Malayalam OTT release date"], "sources": [], "results": 0, "evidence_created": 0, "status": "COMPLETE"})
    monkeypatch.setattr("app.services.research.OTTReconciliationService.reconcile", lambda self, movie_id: "UNKNOWN")

    result = service.execute(run.run_id)
    assert result["trigger_type"] == "ADMIN_MANUAL"
    assert result["status"] == "COMPLETE"
    assert result["result"] == "NOT_FOUND"
    assert result["queries_attempted"] == ["Example Film Malayalam OTT release date"]
    assert {"TMDB", "OMDb", "FakeSearch"} <= set(result["providers_attempted"])
    assert database.query(ResearchRun).filter_by(run_id=run.run_id, active_key=None).one()


def test_failed_queue_releases_research_concurrency_lock(database):
    service = ResearchPipelineService(database)
    run, _ = service.create_run(
        movie_id=1,
        trigger_type="ADMIN_MANUAL",
        initiated_by="admin",
        category="FULL",
    )
    failed = service.fail_queued_run(run.run_id, "queue", RuntimeError("broker unavailable"))
    assert failed["status"] == "FAILED"
    assert failed["result"] == "FAILED"
    assert failed["errors"] == [{"step": "queue", "error": "broker unavailable"}]
    replacement, created = service.create_run(
        movie_id=1,
        trigger_type="ADMIN_RETRY",
        initiated_by="admin",
        category="FULL",
    )
    assert created is True
    assert replacement.run_id != run.run_id


def test_final_worker_failure_releases_research_lock(database, monkeypatch):
    service = ResearchPipelineService(database)
    run, _ = service.create_run(
        movie_id=1,
        trigger_type="ADMIN_MANUAL",
        initiated_by="admin",
        category="FULL",
    )

    class NonClosingSession:
        def __getattr__(self, name):
            return getattr(database, name)

        def close(self):
            pass

    monkeypatch.setattr("app.database.connection.SessionLocal", NonClosingSession)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify",
        lambda *_args, **_kwargs: False,
    )
    notify_task_failure(
        sender=SimpleNamespace(name="research.movie"),
        exception=RuntimeError("final worker failure"),
        args=(run.run_id, "full"),
        kwargs={},
    )
    database.expire_all()
    failed = database.query(ResearchRun).filter_by(run_id=run.run_id).one()
    assert failed.status == "FAILED"
    assert failed.active_key is None
    assert failed.errors[-1] == {
        "step": "worker",
        "error": "final worker failure",
    }


def test_research_history_admin_api_separates_manual_and_automated(database, monkeypatch):
    manual, _ = ResearchPipelineService(database).create_run(movie_id=1, trigger_type="ADMIN_MANUAL", initiated_by="admin", category="OTT")
    manual.status, manual.result, manual.active_key = "COMPLETE", "NO_CHANGE", None
    automated, _ = ResearchPipelineService(database).create_run(movie_id=2, trigger_type="AUTOMATED_SCHEDULE", initiated_by="celery:beat", category="OTT")
    automated.status, automated.result, automated.active_key = "COMPLETE", "UPDATED", None
    database.commit()
    client = _admin_client(database, monkeypatch)
    try:
        assert client.get("/api/v1/admin/research-history?tab=manual").json()["total"] == 1
        scheduled = client.get("/api/v1/admin/research-history?tab=automated").json()
        assert scheduled["total"] == 1
        detail = client.get(f"/api/v1/admin/research-history/{automated.run_id}")
        assert detail.status_code == 200
        assert detail.json()["trigger_type"] == "AUTOMATED_SCHEDULE"
        assert "evidence" in detail.json() and "decisions" in detail.json()
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_request_detail_exposes_research_and_notification_history(database, monkeypatch):
    request = MovieRequest(request_id="REQ-RESEARCH", movie_name="Example Film", verified_title="Example Film", external_movie_id=101, email="viewer@example.test", local_movie_id=1)
    database.add(request)
    database.commit()
    run, _ = ResearchPipelineService(database).create_run(movie_id=1, request_id=request.request_id, trigger_type="MOVIE_REQUEST", initiated_by="movie-request", category="FULL")
    run.status, run.result, run.active_key = "COMPLETE", "NO_CHANGE", None
    database.commit()
    client = _admin_client(database, monkeypatch)
    try:
        detail = client.get("/api/v1/admin/requests/REQ-RESEARCH").json()
        assert detail["research_history"][0]["run_id"] == run.run_id
        assert detail["notification_history"] == []
        assert detail["user_email_history"] == []
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_omdb_diagnostic_lists_only_missing_names(database, monkeypatch):
    monkeypatch.setattr(settings, "IMDB_RATING_PROVIDER", "")
    monkeypatch.setattr(settings, "IMDB_RATING_API_URL", "")
    monkeypatch.setattr(settings, "IMDB_RATING_API_KEY", "")
    status = IMDbRatingRefreshService(database).health()
    assert status["configured"] is False
    assert status["status"] == "NOT_CONFIGURED"
    assert status["missing"] == ["IMDB_RATING_PROVIDER", "IMDB_RATING_API_URL", "IMDB_RATING_API_KEY"]
    assert "key" not in str(status).lower().replace("api_key", "")


def test_discord_adapter_is_preferred_without_exposing_shared_secret(database, monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "DISCORD_BOT_ENDPOINT", "http://127.0.0.1:8765/movie-events")
    monkeypatch.setattr(settings, "DISCORD_BOT_SHARED_SECRET", "private-shared-secret")
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "https://discord.example/fallback")
    monkeypatch.setattr("app.services.notification_service.httpx.post", lambda url, **kwargs: calls.append((url, kwargs)) or SimpleNamespace(raise_for_status=lambda: None))
    assert NotificationService(database)._discord("A safe movie update") is True
    assert calls[0][0] == "http://127.0.0.1:8765/movie-events"
    assert calls[0][1]["json"]["source"] == "indian-ott-tracker"
    assert "private-shared-secret" not in str(calls[0][1]["json"])
    assert NotificationService.discord_method()["method"] == "EXISTING_BOT_ADAPTER"
