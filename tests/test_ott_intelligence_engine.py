"""Regression coverage for the evidence-first India OTT intelligence engine."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.models.movie import Movie
from app.models.operations import OttEvidence, OperationState
from app.models.ott_availability import OttAvailability
from app.models.ott_intelligence import (
    OttAvailabilityObservation,
    OttGoldSetCase,
    OttProviderHealth,
)
from app.services.operations import OttResearchService
from app.services.ott.gold_set import OttGoldSetService
from app.services.ott.intelligence import OTTIntelligenceService
from app.services.ott.matching import MovieMatchService
from app.services.ott.provider_controls import OttProviderControlService
from app.services.ott.providers.base import (
    NormalizedOttEvidence,
    ProviderError,
    ProviderQuotaExhausted,
)


def _evidence(
    *,
    source_type="NEWS",
    source_name="News source",
    platform="Prime Video",
    release_date=None,
    fact_type="ANNOUNCEMENT",
    confidence=80,
):
    return dict(
        movie_id=2,
        platform=platform,
        release_date=release_date,
        source_url=f"https://{source_name.casefold().replace(' ', '')}.example/movie",
        source_name=source_name,
        source_type=source_type,
        confidence=confidence,
        fact_type=fact_type,
        availability_type="SUBSCRIPTION",
        movie_match_confidence=100,
        platform_confidence=confidence if platform else 0,
        date_confidence=confidence if release_date else 0,
    )


def test_availability_never_becomes_release_date(database):
    service = OttResearchService(database)
    service.record_evidence(**_evidence(source_type="JUSTWATCH_TMDB", fact_type="AVAILABILITY", release_date=None, confidence=75))
    service.record_evidence(**_evidence(source_type="STREAMING_AVAILABILITY", source_name="Availability API", fact_type="AVAILABILITY", release_date=None, confidence=75))

    canonical = database.query(OttAvailability).filter_by(movie_id=2, provider="Prime Video").one()
    assert canonical.verification_status == "PLATFORM_CONFIRMED"
    assert canonical.release_state == "OBSERVED_AVAILABLE"
    assert canonical.ott_release_date is None
    assert canonical.date_confidence == 0


def test_tmdb_digital_date_is_evidence_only(database):
    candidate = date.today() + timedelta(days=5)
    evidence = OttResearchService(database).record_evidence(
        **_evidence(
            source_type="TMDB",
            source_name="TMDB digital releases",
            platform=None,
            release_date=candidate,
            fact_type="DIGITAL_DATE",
            confidence=35,
        )
    )
    assert evidence.fact_type == "DIGITAL_DATE"
    assert database.query(OttAvailability).filter_by(movie_id=2).count() == 0


def test_two_credible_disagreeing_dates_become_conflicting(database):
    service = OttResearchService(database)
    first = date.today() + timedelta(days=5)
    service.record_evidence(**_evidence(source_name="Publication A", release_date=first))
    second = service.record_evidence(**_evidence(source_name="Publication B", release_date=first + timedelta(days=7)))

    canonical = database.query(OttAvailability).filter_by(movie_id=2, provider="Prime Video").one()
    assert second.status == "CONFLICTING"
    assert canonical.verification_status == "NEEDS_REVIEW"
    assert canonical.ott_release_date is None


def test_official_date_supersedes_earlier_news_consensus(database):
    service = OttResearchService(database)
    predicted = date.today() + timedelta(days=5)
    official = predicted + timedelta(days=1)
    service.record_evidence(**_evidence(source_name="Publication A", release_date=predicted))
    service.record_evidence(**_evidence(source_name="Publication B", release_date=predicted))
    service.record_evidence(
        **_evidence(
            source_type="OFFICIAL_PLATFORM",
            source_name="Official Prime Video",
            release_date=official,
            confidence=100,
        )
    )

    canonical = database.query(OttAvailability).filter_by(movie_id=2, provider="Prime Video").one()
    older = database.query(OttEvidence).filter(OttEvidence.movie_id == 2, OttEvidence.release_date == predicted).all()
    assert canonical.ott_release_date == official
    assert canonical.verification_status == "CONFIRMED"
    assert all(row.status == "SUPERSEDED" for row in older)


def test_manual_lock_survives_later_official_disagreement(database):
    service = OttResearchService(database)
    manual_date = date.today() + timedelta(days=4)
    service.manually_verify(
        2,
        platform="Netflix",
        release_date=manual_date,
        source_url="https://netflix.com/in/title/example",
    )
    service.record_evidence(
        **_evidence(
            source_type="OFFICIAL_PLATFORM",
            source_name="Official Netflix",
            platform="Netflix",
            release_date=manual_date + timedelta(days=2),
            confidence=100,
        )
    )
    canonical = database.query(OttAvailability).filter_by(movie_id=2, provider="Netflix").one()
    assert canonical.locked_by_admin is True
    assert canonical.ott_release_date == manual_date


def test_multiple_platforms_keep_earliest_original_premiere(database):
    service = OttResearchService(database)
    first = date.today() - timedelta(days=30)
    later = date.today()
    service.record_evidence(**_evidence(source_type="OFFICIAL_PLATFORM", source_name="Official Netflix", platform="Netflix", release_date=first, confidence=100))
    service.record_evidence(**_evidence(source_type="OFFICIAL_PLATFORM", source_name="Official Prime", platform="Prime Video", release_date=later, confidence=100))
    rows = database.query(OttAvailability).filter_by(movie_id=2).all()
    original = next(row for row in rows if row.is_original_premiere)
    assert original.provider == "Netflix"
    assert original.ott_release_date == first


def test_identity_match_rejects_wrong_year_and_language(database):
    database.add_all(
        [
            Movie(tmdb_id=9001, title="The Hero", release_date=date(2020, 1, 1), original_language="ml"),
            Movie(tmdb_id=9002, title="The Hero", release_date=date(2024, 1, 1), original_language="ta"),
        ]
    )
    database.commit()
    matcher = MovieMatchService(database)
    exact = matcher.match(SimpleNamespace(tmdb_id=None, imdb_id=None, title="The Hero", original_title=None, year=2024, language="ta", runtime_minutes=None, directors=(), cast=()))
    wrong = matcher.match(SimpleNamespace(tmdb_id=None, imdb_id=None, title="The Hero", original_title=None, year=2010, language="kn", runtime_minutes=None, directors=(), cast=()))
    assert exact.status == "MATCHED"
    assert exact.movie.tmdb_id == 9002
    assert wrong.status == "REJECTED"


def test_provider_budget_and_circuit_breaker_are_isolated(database, monkeypatch):
    class Provider:
        name = "budgeted"
        enabled = True
        configured = True
        daily_limit = 1
        monthly_limit = 1

    controls = OttProviderControlService(database)
    assert controls.execute(Provider(), lambda: []) == []
    with pytest.raises(ProviderQuotaExhausted):
        controls.execute(Provider(), lambda: [])

    class FailingProvider(Provider):
        name = "failing"
        daily_limit = 10
        monthly_limit = 10

    monkeypatch.setattr(settings, "OTT_PROVIDER_FAILURE_THRESHOLD", 2)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            controls.execute(FailingProvider(), lambda: (_ for _ in ()).throw(RuntimeError("temporary outage")))
    with pytest.raises(ProviderError):
        controls.execute(FailingProvider(), lambda: [])
    health = database.query(OttProviderHealth).filter_by(provider="failing").one()
    assert health.status == "DEGRADED"
    assert health.circuit_open_until is not None


def test_one_provider_failure_does_not_stop_other_providers(database, monkeypatch):
    class DownProvider:
        name = "down"
        enabled = configured = True
        daily_limit = monthly_limit = 10

        def fetch_movie(self, movie):
            raise RuntimeError("temporary outage")

    class GoodProvider(DownProvider):
        name = "good"

        def fetch_movie(self, movie):
            return [
                NormalizedOttEvidence(
                    source_type="STREAMING_AVAILABILITY",
                    source_name="Independent availability",
                    fact_type="AVAILABILITY",
                    platform_candidate="Netflix",
                    availability_type="SUBSCRIPTION",
                    source_url="https://availability.example/title",
                    observed_at=datetime.now(timezone.utc),
                    tmdb_id=movie.tmdb_id,
                    title=movie.title,
                    movie_match_confidence=100,
                    platform_confidence=75,
                )
            ]

    monkeypatch.setattr(OTTIntelligenceService, "providers", staticmethod(lambda: [DownProvider(), GoodProvider()]))
    result = OTTIntelligenceService(database).refresh_movie(2)
    assert result["providers"]["down"]["status"] == "DOWN"
    assert result["providers"]["good"]["status"] == "HEALTHY"
    assert result["evidence"] == 1
    assert database.query(OttEvidence).filter_by(movie_id=2, source_type="STREAMING_AVAILABILITY").count() == 1
    assert database.query(OttAvailabilityObservation).filter_by(movie_id=2, source_type="STREAMING_AVAILABILITY").count() == 1


def test_same_day_observations_coalesce_across_sqlite_timezone_behavior(database):
    service = OTTIntelligenceService(database)
    first = NormalizedOttEvidence(
        source_type="JUSTWATCH_TMDB",
        source_name="TMDB watch providers",
        fact_type="AVAILABILITY",
        platform_candidate="Netflix",
        availability_type="SUBSCRIPTION",
        observed_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    second = NormalizedOttEvidence.from_serializable(first.serializable())
    second.observed_at = datetime.now(timezone.utc)
    service._observe(2, first, None)
    database.commit()
    service._observe(2, second, None)
    database.commit()
    assert database.query(OttAvailabilityObservation).filter_by(movie_id=2).count() == 1


def test_gold_set_blocks_automatic_publication_until_manually_verified(database):
    database.add(OttGoldSetCase(movie_id=1, language="ml", category="RECENT", expected_state="UNKNOWN"))
    database.commit()
    result = OttGoldSetService(database).evaluate()
    state = database.query(OperationState).filter_by(name="ott.gold_set_accuracy").one()
    assert result["gate_passed"] is False
    assert result["automatic_publication_enabled"] is False
    assert state.status == "BLOCKED"
