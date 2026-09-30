from datetime import datetime, timezone

from app.models.operations import BackfillRecord, OperationState
from app.models.ott_availability import OttAvailability
from app.models.ott_intelligence import OttAvailabilityObservation
from app.services.ott.catalogue_audit import (
    OPERATION,
    OttCatalogueAuditService,
    ott_completeness_metrics,
)


class _NoWebProvider:
    configured = False


class _NoWeb:
    provider = _NoWebProvider()


class _PlatformCollector:
    def __init__(self, db):
        self.db = db

    def refresh_movie(self, movie_id, **_kwargs):
        if not self.db.query(OttAvailability).filter_by(movie_id=movie_id, country="IN").first():
            self.db.add(
                OttAvailability(
                    movie_id=movie_id,
                    provider="Prime Video",
                    country="IN",
                    watch_type="subscription",
                    verification_status="PLATFORM_CONFIRMED",
                )
            )
        self.db.add(
            OttAvailabilityObservation(
                movie_id=movie_id,
                provider="Prime Video",
                country="IN",
                availability_type="SUBSCRIPTION",
                available=True,
                source_type="JUSTWATCH_TMDB",
                observed_at=datetime.now(timezone.utc),
            )
        )
        self.db.commit()
        return {"providers": {"tmdb_justwatch": {"status": "HEALTHY"}}}


class _UnavailableCollector:
    def refresh_movie(self, movie_id, **_kwargs):
        return {"providers": {"tmdb_justwatch": {"status": "DOWN"}}}


def test_metrics_distinguish_researched_from_complete(database):
    database.add(
        OttAvailabilityObservation(
            movie_id=2,
            provider=None,
            country="IN",
            availability_type="UNKNOWN",
            available=False,
            source_type="JUSTWATCH_TMDB",
            observed_at=datetime.now(timezone.utc),
        )
    )
    database.commit()

    metrics = ott_completeness_metrics(database)["summary"]
    assert metrics["movies"] == 2
    assert metrics["platform_known"] == 1
    assert metrics["platform_known_date_unknown"] == 1
    assert metrics["complete"] == 0
    assert metrics["researched"] == 1
    assert metrics["never_checked"] == 1


def test_catalogue_audit_is_checkpointed_and_idempotent(database):
    service = OttCatalogueAuditService(database)
    service.intelligence = _PlatformCollector(database)
    service.web = _NoWeb()

    first = service.run(batch_size=2)
    second = service.run(batch_size=2)

    assert first["processed"] == 2
    assert first["platforms_added"] == 1
    assert first["dates_added"] == 0
    assert second["processed"] == 0
    assert database.query(BackfillRecord).filter_by(operation=OPERATION, status="DONE").count() == 2
    state = database.query(OperationState).filter_by(name=OPERATION).one()
    assert state.details["baseline"]["movies"] == 2
    assert state.details["after"]["platform_known"] == 2


def test_catalogue_audit_pauses_on_provider_failure_without_poisoning_batch(database):
    service = OttCatalogueAuditService(database)
    service.intelligence = _UnavailableCollector()
    service.web = _NoWeb()

    result = service.run(batch_size=2)

    assert result["processed"] == 0
    assert result["failed"] == 0
    assert result["stopped"] == "PROVIDER_UNAVAILABLE"
    assert database.query(BackfillRecord).filter_by(operation=OPERATION, status="FAILED").count() == 0
    assert database.query(BackfillRecord).filter_by(operation=OPERATION, status="PENDING").count() == 1
