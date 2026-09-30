"""Persistent AI activity, authorization, Discord, and history coverage."""

from datetime import date
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
import pytest

from app.config.settings import settings
from app.core.user_auth import USER_COOKIE, create_user_session, hash_password
from app.database.connection import get_db
from app.main import app
from app.models.ai_research_audit import (
    AIResearchAuditRun,
    AIResearchEvent,
    AIResearchMovieActivity,
    AIResearchSource,
)
from app.models.community_rating import User
from app.models.movie import Movie
from app.models.ott_availability import OttAvailability
from app.services.ai_research_audit import AIResearchAuditService, AIResearchDiscordNotifier
from app.services.ott.ai_research import (
    AIProviderResult,
    AIResearchProviderError,
    FallbackAIResearchProvider,
    OTTResearchAgent,
    ProviderErrorCode,
    ProviderFinding,
    ResearchDecision,
)


class FakeProvider:
    name = "omniroute"
    model = "test-web-model"
    configured = True

    def research(self, identity, queries):
        return AIProviderResult(
            self.name,
            self.model,
            [
                ProviderFinding(
                    source_url="https://news.example.test/example-film",
                    source_title="Example Film OTT release",
                    platform="Netflix",
                    release_date=date.today().isoformat(),
                    region="IN",
                    watch_type="SUBSCRIPTION",
                    evidence_summary="Example Film is streaming on Netflix in India.",
                    identity_confidence=98,
                    platform_confidence=96,
                    date_confidence=93,
                    query=queries[0],
                )
            ],
            queries,
        )


def _valid_inspection(monkeypatch):
    monkeypatch.setattr("app.services.ott.ai_research.source_rank", lambda _url: ("trade_press", 98))
    monkeypatch.setattr(
        "app.services.ott.ai_research.inspect_source",
        lambda _movie, _source: {
            "platform": "Netflix",
            "release_date": date.today().isoformat(),
            "country": "IN",
            "evidence_summary": "Example Film is available on Netflix by subscription in India.",
        },
    )


def test_audit_run_movie_events_sources_and_dry_run_persist(database, monkeypatch):
    _valid_inspection(monkeypatch)
    movie = database.get(Movie, 1)
    before = database.query(OttAvailability).filter_by(movie_id=movie.id).count()
    audit = AIResearchAuditService(database)
    run = audit.create_run(
        run_id="audit-test-run",
        trigger="MANUAL",
        initiated_by="test-admin",
        mode="DRY_RUN",
        provider="omniroute",
        model="test-web-model",
        movies_total=1,
    )
    report = OTTResearchAgent(database, FakeProvider(), audit).research_movie(
        movie.id, dry_run=True, research_run_id=run.run_id
    )
    audit.complete_run(run.run_id)

    activity = database.query(AIResearchMovieActivity).filter_by(run_id=run.run_id).one()
    source = database.query(AIResearchSource).filter_by(movie_activity_id=activity.id).one()
    events = database.query(AIResearchEvent).filter_by(movie_activity_id=activity.id).order_by(AIResearchEvent.sequence).all()
    assert report.decision == ResearchDecision.AUTO_ACCEPT
    assert activity.dry_run is True and activity.database_changed is False
    assert activity.queries and activity.platforms_found == ["Netflix"]
    assert source.accepted is True and source.fetch_status == "FETCHED"
    assert [item.sequence for item in events] == list(range(1, len(events) + 1))
    assert {"MOVIE_STARTED", "QUERY_CREATED", "SEARCH_STARTED", "SOURCE_ACCEPTED", "DECISION_MADE", "DATABASE_WRITE_SKIPPED", "MOVIE_COMPLETED"} <= {item.event_type for item in events}
    assert database.query(OttAvailability).filter_by(movie_id=movie.id).count() == before
    database.refresh(run)
    assert run.status == "COMPLETED" and run.movies_processed == 1
    assert run.sources_discovered == 1 and run.sources_inspected == 1


def test_provider_failure_creates_failure_activity_not_empty_evidence(database):
    class BrokenProvider:
        name = "omniroute"
        model = "test-web-model"
        configured = True

        def research(self, _identity, _queries):
            raise AIResearchProviderError(ProviderErrorCode.RATE_LIMITED, "rate limited")

    report = OTTResearchAgent(database, BrokenProvider()).research_movie(1, dry_run=True)
    activity = database.query(AIResearchMovieActivity).one()
    assert report.decision == ResearchDecision.DEFERRED_PROVIDER_ERROR
    assert activity.error_type == "PROVIDER_RATE_LIMITED"
    assert activity.decision == "DEFERRED_PROVIDER_ERROR"
    assert database.query(AIResearchSource).count() == 0
    assert "PROVIDER_RATE_LIMIT" in {item.event_type for item in database.query(AIResearchEvent)}


def test_provider_fallback_is_persisted_and_counted(database):
    class TimeoutProvider:
        name = "primary"
        model = "primary-model"
        configured = True

        def research(self, _identity, _queries):
            raise AIResearchProviderError(ProviderErrorCode.TIMEOUT, "timed out")

    class EmptyFallback:
        name = "fallback"
        model = "fallback-model"
        configured = True

        def research(self, _identity, queries):
            return AIProviderResult(self.name, self.model, [], queries, [])

    provider = FallbackAIResearchProvider([TimeoutProvider(), EmptyFallback()])
    OTTResearchAgent(database, provider).research_movie(1, dry_run=True)

    run = database.query(AIResearchAuditRun).one()
    event_types = {item.event_type for item in database.query(AIResearchEvent)}
    assert run.fallback_count == 1
    assert {"PROVIDER_ERROR", "PROVIDER_FALLBACK"} <= event_types


def test_secret_redaction_at_audit_boundary(database, monkeypatch):
    monkeypatch.setattr(settings, "AI_RESEARCH_API_KEY", "omniroute-private-key")
    monkeypatch.setattr(settings, "AI_RESEARCH_DISCORD_WEBHOOK_URL", "https://discord.example.test/private-hook")
    audit = AIResearchAuditService(database)
    run = audit.create_run(run_id="redaction-run", movies_total=0)
    audit.event(
        run.run_id,
        "PROVIDER_ERROR",
        "Authorization: Bearer omniroute-private-key",
        metadata={"api_key": "omniroute-private-key", "url": "https://example.test/?token=omniroute-private-key"},
    )
    event = database.query(AIResearchEvent).filter_by(event_type="PROVIDER_ERROR").one()
    rendered = f"{event.message} {event.metadata_json}"
    assert "omniroute-private-key" not in rendered
    assert "[redacted]" in rendered


def test_admin_api_authorization_filters_pagination_and_history(database):
    audit = AIResearchAuditService(database)
    run = audit.create_run(run_id="history-run", movies_total=3)
    for index in range(3):
        identity = SimpleNamespace(
            local_movie_id=100 + index,
            tmdb_id=1000 + index,
            imdb_id=f"tt000{index}",
            title=f"History Film {index}",
            original_title=None,
            release_year=2026,
            original_language="ml",
            production_countries=("IN",),
        )
        audit.start_movie(run.run_id, identity, dry_run=True, provider="omniroute", model="test-web-model")

    normal = User(email="viewer@example.test", password_hash=hash_password("Viewer-pass-123"), role="USER")
    admin = User(email="admin@example.test", password_hash=hash_password("Admin-pass-123"), role="ADMIN")
    database.add_all([normal, admin])
    database.commit()

    def override_db():
        yield database

    app.dependency_overrides[get_db] = override_db
    anonymous = TestClient(app)
    try:
        assert anonymous.get("/api/v1/admin/ai-activity/overview").status_code == 401
        with TestClient(app) as viewer:
            viewer.cookies.set(USER_COOKIE, create_user_session(normal.id))
            assert viewer.get("/api/v1/admin/ai-activity/overview").status_code == 403
        with TestClient(app) as administrator:
            administrator.cookies.set(USER_COOKIE, create_user_session(admin.id))
            listing = administrator.get("/api/v1/admin/ai-activity/movies?page=1&page_size=2&title=History")
            assert listing.status_code == 200
            assert listing.json()["total"] == 3
            assert listing.json()["pages"] == 2
            detail = administrator.get("/api/v1/admin/ai-activity/runs/history-run")
            assert detail.status_code == 200 and detail.json()["movies"]["total"] == 3
            activity_id = listing.json()["items"][0]["id"]
            movie = administrator.get(f"/api/v1/admin/ai-activity/movies/{activity_id}")
            assert movie.status_code == 200 and movie.json()["movie"]["title"].startswith("History Film")
            live = administrator.get("/api/v1/admin/ai-activity/live?after_event_id=0")
            assert live.status_code == 200 and live.json()["events"]
    finally:
        anonymous.close()
        app.dependency_overrides.clear()


def test_discord_payload_and_rate_limit_are_bounded(monkeypatch):
    monkeypatch.setattr(settings, "AI_RESEARCH_DISCORD_ENABLED", True)
    monkeypatch.setattr(settings, "AI_RESEARCH_DISCORD_WEBHOOK_URL", "https://discord.example.test/webhook")
    calls = []

    def limited(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(429, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.ai_research_audit.httpx.post", limited)
    with pytest.raises(httpx.HTTPStatusError):
        AIResearchDiscordNotifier().send("bounded notification")
    assert len(calls) == 2
    assert calls[0][1]["timeout"] <= 10


def test_discord_timeout_records_failure_and_does_not_stop_research(database, monkeypatch):
    _valid_inspection(monkeypatch)
    monkeypatch.setattr(settings, "AI_RESEARCH_DISCORD_ENABLED", True)
    monkeypatch.setattr(settings, "AI_RESEARCH_DISCORD_WEBHOOK_URL", "https://discord.example.test/webhook")
    monkeypatch.setattr(
        "app.services.ai_research_audit.httpx.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("discord timeout")),
    )
    report = OTTResearchAgent(database, FakeProvider()).research_movie(1, dry_run=True)
    assert report.decision == ResearchDecision.AUTO_ACCEPT
    failures = database.query(AIResearchEvent).filter_by(event_type="DISCORD_NOTIFICATION_FAILED").all()
    assert failures
    assert all("webhook" not in str(item.metadata_json).lower() for item in failures)
