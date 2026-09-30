import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from redis import Redis
from sqlalchemy import case, exists, func, select
from app.workers.celery_app import celery_app
from app.database.connection import SessionLocal
from app.services.operations import (
    DataHealthService,
    OttResearchService,
    ResearchUsageService,
)
from app.services.release_status import (
    INDIAN_ORIGINAL_LANGUAGE_CODES,
    ReleaseStatusService,
    site_date,
)
from app.services.image_fallback import ImageFallbackService
from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.services.tmdb.client import TMDbRequestError
from app.services.ott_providers import (
    configured_ott_provider,
    inspect_source,
    source_rank,
)
from app.models.movie import Movie
from app.models.operations import (
    DataQualityIssue,
    MovieRequest,
    NotificationLog,
    OperationState,
    OttEvidence,
)
from app.models.ott_availability import OttAvailability

log = logging.getLogger(__name__)

CATALOGUE_RECONCILIATION_LOCK = "locks:tmdb:catalogue_reconciliation"
CATALOGUE_RECONCILIATION_LOCK_TTL = 12 * 60 * 60


def _acquire_catalogue_reconciliation_lock() -> tuple[Redis, str] | None:
    client = Redis.from_url(settings.REDIS_URL)
    token = str(uuid.uuid4())
    acquired = client.set(
        CATALOGUE_RECONCILIATION_LOCK,
        token,
        nx=True,
        ex=CATALOGUE_RECONCILIATION_LOCK_TTL,
    )
    return (client, token) if acquired else None


def _release_catalogue_reconciliation_lock(client: Redis, token: str) -> None:
    client.eval(
        """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """,
        1,
        CATALOGUE_RECONCILIATION_LOCK,
        token,
    )


@celery_app.task(name="content_safety.scan", bind=True)
def content_safety_scan(self, batch_size: int = 500, continuous: bool = False):
    from app.services.content_safety import ContentSafetyService

    result = _run(lambda db: ContentSafetyService(db).scan_batch(batch_size, bulk=True))
    if continuous and not result.get("complete"):
        self.apply_async(kwargs={"batch_size": batch_size, "continuous": True}, countdown=2)
    return result


@celery_app.task(
    name="content_safety.notification",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def content_safety_notification(self, audit_id: int):
    from app.services.content_safety import ContentSafetyService

    sent = _run(lambda db: ContentSafetyService(db).notify_audit(audit_id))
    if not sent:
        raise self.retry(exc=RuntimeError("Content-safety Discord delivery failed"))
    return {"audit_id": audit_id, "status": "SENT"}


@celery_app.task(
    name="content_safety.digest_notification",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def content_safety_digest_notification(self, audit_ids: list[int], scanned: int):
    from app.services.content_safety import ContentSafetyService

    sent = _run(lambda db: ContentSafetyService(db).notify_digest(audit_ids, scanned))
    if not sent:
        raise self.retry(exc=RuntimeError("Content-safety Discord digest delivery failed"))
    return {"audit_ids": audit_ids, "status": "DIGEST_SENT"}


def _json_safe(value):
    """Make service reports safe for PostgreSQL JSON without losing audit text."""
    return json.loads(json.dumps(value, default=str))


def _changed_batch_fields(result: dict) -> list[str]:
    # Merely checking/processing a record is not a database data change.
    keys = ("updated", "evidence_created", "queued", "transitions", "new_movies_imported")
    return [key for key in keys if result.get(key)]


def _run(service):
    db = SessionLocal()
    try:
        return service(db)
    finally:
        db.close()


def _logged_automated(category: str, active_key: str, runner):
    """Persist scheduled batch provenance without changing its underlying service."""
    from app.models.research import ResearchRun
    from app.services.research import ResearchPipelineService

    def run(db):
        service = ResearchPipelineService(db)
        record, created = service.create_run(
            trigger_type="AUTOMATED_SCHEDULE",
            initiated_by="celery:beat",
            category=category,
            active_key=active_key,
        )
        if not created:
            return service.serialize(record) | {"skipped": "already_running"}
        record.status, record.started_at = "RUNNING", datetime.now(timezone.utc)
        db.commit()
        try:
            result = runner(db, record.run_id)
            record = db.query(ResearchRun).filter_by(run_id=record.run_id).one()
            changed_fields = _changed_batch_fields(result)
            record.status = "COMPLETE"
            record.result = "UPDATED" if changed_fields else "NO_CHANGE"
            record.completed_at, record.active_key = datetime.now(timezone.utc), None
            record.details = {"batch": _json_safe(result)}
            record.database_changes = changed_fields
            record.evidence_created = int(result.get("evidence_created") or 0)
            record.web_searches_attempted = int(result.get("queries") or 0)
            db.commit()
            return service.serialize(record)
        except Exception as exc:
            db.rollback()
            record = db.query(ResearchRun).filter_by(run_id=record.run_id).one()
            record.status, record.result, record.active_key = "FAILED", "FAILED", None
            record.completed_at = datetime.now(timezone.utc)
            record.errors = [{"step": category.lower(), "error": sanitize_error(exc)}]
            db.commit()
            raise
    return _run(run)


def _continue(task, result, *, batch_size=None, countdown=2):
    """Continue one checkpointed chain without creating unbounded parallel work."""
    if (
        result.get("complete") is False
        and not result.get("stopped")
        and result.get("configured", True)
    ):
        db = SessionLocal()
        state = None
        try:
            state = (
                db.query(OperationState).filter_by(name=result.get("operation")).first()
            )
            if state:
                state.status = "RUNNING"
                db.commit()
            task.apply_async(
                kwargs={"batch_size": batch_size, "continuous": True},
                countdown=countdown,
            )
        except Exception as exc:
            if state:
                state.status = "FAILED"
                state.last_failure_at = datetime.now(timezone.utc)
                state.last_error = sanitize_error(exc)
                db.commit()
            raise
        finally:
            db.close()
    return result


def _locked_backfill(operation: str, callback):
    """Allow only one chain step per operation, including after redelivery."""
    client = Redis.from_url(settings.REDIS_URL)
    key = f"backfill-lock:{operation}"
    token = uuid.uuid4().hex
    if not client.set(key, token, nx=True, ex=3600):
        return {"operation": operation, "complete": True, "duplicate_skipped": True}
    try:
        return _run(callback)
    finally:
        client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            token,
        )


@celery_app.task(
    name="operations.data_health",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def data_health():
    return _run(lambda db: DataHealthService(db).scan())


@celery_app.task(
    name="operations.ott_queue",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_queue():
    return _run(lambda db: OttResearchService(db).queue_missing())


@celery_app.task(
    bind=True,
    name="operations.release_status",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def release_status(self, batch_size=1000, restart_completed=False):
    result = _run(
        lambda db: ReleaseStatusService(db).classify_batch(
            batch_size, restart_completed=restart_completed
        )
    )
    if not result.get("complete"):
        self.apply_async(
            kwargs={"batch_size": batch_size, "restart_completed": False},
            countdown=2,
        )
    return result


@celery_app.task(
    name="operations.image_recovery",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def image_recovery():
    return _run(lambda db: ImageFallbackService(db).recover_batch())


@celery_app.task(
    name="operations.image_health",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def image_health():
    return _run(lambda db: ImageFallbackService(db).scan())


@celery_app.task(
    name="movies.discovery",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def movie_discovery():
    from app.services.movie_discovery import MovieDiscoveryService
    return _logged_automated(
        "DISCOVERY", "scheduled:movie_discovery",
        lambda db, run_id: MovieDiscoveryService(db).run_regular(
            trigger_type="AUTOMATED_SCHEDULE", initiated_by="celery:beat", research_run_id=run_id
        ),
    )


@celery_app.task(
    name="movies.discovery_weekly",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def movie_discovery_weekly():
    from app.services.movie_discovery import MovieDiscoveryService
    return _logged_automated(
        "DISCOVERY", "scheduled:movie_discovery_weekly",
        lambda db, run_id: MovieDiscoveryService(db).run_weekly(
            trigger_type="AUTOMATED_SCHEDULE", initiated_by="celery:beat", research_run_id=run_id
        ),
    )


@celery_app.task(
    name="tmdb.catalogue_reconciliation",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def tmdb_catalogue_reconciliation():
    """Rescan supported TMDB catalogues with durable, isolated cursors."""
    lock = _acquire_catalogue_reconciliation_lock()
    if lock is None:
        log.info("job=tmdb.catalogue_reconciliation outcome=skipped_active_run")
        return {"status": "SKIPPED_ACTIVE_RUN", "processed": 0, "inserted": 0, "errors": 0}

    client, token = lock
    try:
        from app.services.languages import LANGUAGE_CONFIG
        from app.services.tmdb.bulk_importer import SUPPORTED_LANGUAGES, TMDbBulkImporter

        priority = ("ml", "ta", "te", "hi", "kn", "ko", "ja", "en", "fr")
        enabled = {
            language.code
            for language in LANGUAGE_CONFIG
            if language.enabled and language.code in SUPPORTED_LANGUAGES
        }
        languages = [language for language in priority if language in enabled]
        languages.extend(sorted(enabled.difference(languages)))
        current_year = date.today().year
        vote_floors = {language: 0 for language in languages}
        segments: dict[str, dict] = {}

        # Independent future-year cursors prevent a large current-year catalogue
        # from starving the following two upcoming years.
        for year in range(current_year, current_year + 3):
            segments[str(year)] = TMDbBulkImporter(
                languages=languages,
                start_year=year,
                end_year=year,
                end_date=date(year, 12, 31),
                operation_prefix=f"movies.catalogue_reconciliation.{year}",
                minimum_vote_counts=vote_floors,
            ).run_import(
                restart_completed=False,
                max_pages=5,
                max_new_movies_per_language=10,
            )

        if current_year > 2011:
            segments["history"] = TMDbBulkImporter(
                languages=languages,
                start_year=2011,
                end_year=current_year - 1,
                end_date=date(current_year - 1, 12, 31),
                operation_prefix="movies.catalogue_reconciliation.history",
                minimum_vote_counts=vote_floors,
            ).run_import(
                restart_completed=False,
                max_pages=5,
                max_new_movies_per_language=10,
            )

        totals = {
            key: sum(int(result.get(key) or 0) for result in segments.values())
            for key in (
                "processed",
                "inserted",
                "existing",
                "filtered",
                "duplicate_imdb",
                "errors",
            )
        }
        rejected_by_reason: dict[str, int] = {}
        for result in segments.values():
            for reason, count in (result.get("rejected_by_reason") or {}).items():
                rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + int(count)
        return {
            **totals,
            "rejected_by_reason": rejected_by_reason,
            "segments": segments,
        }
    finally:
        _release_catalogue_reconciliation_lock(client, token)


@celery_app.task(
    name="movies.discovery_manual",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def movie_discovery_manual(deep: bool = False, research_run_id: str | None = None):
    from app.services.movie_discovery import MovieDiscoveryService
    from app.models.research import ResearchRun

    def run(db):
        service = MovieDiscoveryService(db)
        try:
            result = (
                service.run_weekly(trigger_type="DISCOVERY_DEEP", initiated_by="admin", research_run_id=research_run_id)
                if deep else
                service.run_regular(trigger_type="DISCOVERY_REGULAR", initiated_by="admin", research_run_id=research_run_id)
            )
            if research_run_id:
                record = db.query(ResearchRun).filter_by(run_id=research_run_id).first()
                if record:
                    record.status = "COMPLETE" if result.get("status") == "COMPLETE" else "FAILED"
                    record.result = "UPDATED" if result.get("new_movies_imported", 0) else "NO_CHANGE"
                    record.completed_at = datetime.now(timezone.utc)
                    record.active_key = None
                    record.details = {"discovery": _json_safe(result)}
                    record.database_changes = ["movies_imported"] if result.get("new_movies_imported", 0) else []
                    db.commit()
            return result
        except Exception as exc:
            db.rollback()
            if research_run_id:
                record = db.query(ResearchRun).filter_by(run_id=research_run_id).first()
                if record:
                    # Keep the lock while Celery retries; the final failure signal
                    # releases it if retry recovery is exhausted.
                    record.status, record.result = "RETRYING", None
                    record.errors = [
                        *(record.errors or []),
                        {"step": "discovery", "error": sanitize_error(exc)},
                    ]
                    db.commit()
            raise
    return _run(run)


@celery_app.task(
    name="tmdb.metadata_enrichment",
    autoretry_for=(),
)
def metadata_enrichment():
    def run(db):
        from app.services.movie_metadata_service import MovieMetadataService
        from app.services.tmdb.client import metadata_request_context

        state = db.query(OperationState).filter_by(name="metadata_enrichment").first()
        if not state:
            state = OperationState(name="metadata_enrichment")
            db.add(state)
            db.flush()
        # Keep failed movies retryable without allowing them to starve the
        # normal cursor scan. Each scheduled run has at most five retry slots.
        now = datetime.now(timezone.utc)
        retry_ids = []
        for issue in db.query(DataQualityIssue).filter_by(
            issue_type="TMDB_METADATA_RETRY_PENDING", resolved_at=None
        ).order_by(DataQualityIssue.id):
            deadline = datetime.fromisoformat(json.loads(issue.detail)["next_retry_at"])
            if deadline <= now and issue.movie_id <= state.cursor:
                retry_ids.append(issue.movie_id)
            if len(retry_ids) == 5:
                break
        movies = (
            db.query(Movie)
            .filter(Movie.id > state.cursor)
            .order_by(Movie.id)
            .limit(20)
            .all()
        )
        cycle_complete = not movies
        movies = db.query(Movie).filter(Movie.id.in_(retry_ids)).all() + movies
        if not movies:
            state.cursor = 0
            db.commit()
            return {"processed": 0, "cycle_complete": True}
        service = MovieMetadataService(db)
        ott_service = OttResearchService(db, settings.OTT_CONFIRMATION_THRESHOLD)
        processed = failed = 0
        for movie in movies:
            movie_id, tmdb_id = movie.id, movie.tmdb_id
            pending = db.query(DataQualityIssue).filter_by(
                movie_id=movie_id, issue_type="TMDB_METADATA_RETRY_PENDING", resolved_at=None
            ).first()
            if pending and datetime.fromisoformat(json.loads(pending.detail)["next_retry_at"]) > now:
                state.cursor = max(state.cursor, movie_id)
                db.commit()
                continue
            # Skip movies with an open TMDB_NOT_FOUND issue
            if (
                db.query(DataQualityIssue)
                .filter_by(
                    movie_id=movie.id, issue_type="TMDB_NOT_FOUND", resolved_at=None
                )
                .first()
            ):
                state.cursor = max(state.cursor, movie_id)
                db.commit()
                continue

            try:
                with metadata_request_context(movie_id, tmdb_id):
                    service.enrich_movie(movie)
            except TMDbRequestError as exc:
                db.rollback()
                # Persist an existing state before enrichment so rollback
                # cannot discard a newly created cursor record.
                state = db.query(OperationState).filter_by(name="metadata_enrichment").first()
                if state is None:
                    state = OperationState(name="metadata_enrichment", cursor=0, processed_count=0)
                    db.add(state)
                kind = ("TMDB_NOT_FOUND" if exc.status_code == 404 else
                        "TMDB_METADATA_RETRY_PENDING" if exc.retryable else "TMDB_METADATA_NEEDS_REVIEW")
                issue = db.query(DataQualityIssue).filter_by(
                    movie_id=movie_id, issue_type=kind, resolved_at=None
                ).first()
                if issue is None:
                    issue = DataQualityIssue(movie_id=movie_id, issue_type=kind, severity="warning")
                    db.add(issue)
                delay = max(900, getattr(exc, "retry_after", 0))
                issue.detail = json.dumps({
                    "endpoint": exc.endpoint.split("?", 1)[0], "status": exc.status_code,
                    "attempts": exc.attempts,
                    "next_retry_at": (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(),
                })
                if pending is not None and kind != "TMDB_METADATA_RETRY_PENDING":
                    pending.resolved_at = datetime.now(timezone.utc)
                state.cursor = max(state.cursor, movie_id)
                state.last_failure_at = datetime.now(timezone.utc)
                state.last_error = str(exc)
                db.commit()
                failed += 1
                log.warning(
                    "job=tmdb.metadata_enrichment movie_id=%s tmdb_id=%s endpoint=%s "
                    "status=%s attempt=%s max_attempts=5 next_retry_delay=%s outcome=%s",
                    movie_id, tmdb_id, exc.endpoint, exc.status_code, exc.attempts,
                    delay if exc.retryable else 0, kind,
                )
                continue

            ReleaseStatusService(db).classify_movie(movie)
            ott_service.queue_movie(movie.id)
            if pending:
                pending.resolved_at = datetime.now(timezone.utc)
            state.cursor = max(state.cursor, movie.id)
            state.processed_count += 1
            processed += 1
            db.commit()
        if cycle_complete:
            state.cursor = 0
        state.last_success_at = datetime.now(timezone.utc)
        db.commit()
        return {
            "processed": processed,
            "failed": failed,
            "cursor": state.cursor,
            "cycle_complete": cycle_complete,
        }

    return _run(run)


@celery_app.task(
    name="ratings.imdb_refresh",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def imdb_rating_refresh():
    from app.services.rating_provider import IMDbRatingRefreshService
    return _logged_automated(
        "IMDB", "scheduled:imdb_refresh",
        lambda db, _run_id: IMDbRatingRefreshService(db).refresh(),
    )


@celery_app.task(bind=True, name="ratings.imdb_id_backfill")
def imdb_id_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import IMDbIdRecoveryService

    result = _locked_backfill("ratings.imdb_id_backfill", lambda db: IMDbIdRecoveryService(db).run(batch_size))
    return (
        _continue(self, result, batch_size=batch_size, countdown=5)
        if continuous
        else result
    )


@celery_app.task(bind=True, name="tmdb.metadata_backfill")
def metadata_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import MetadataBackfillService

    result = _locked_backfill("tmdb.metadata_backfill", lambda db: MetadataBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(bind=True, name="tmdb.person_backfill")
def person_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import PersonBackfillService

    result = _locked_backfill("tmdb.person_backfill", lambda db: PersonBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(bind=True, name="operations.image_backfill")
def image_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import ImageBackfillService

    result = _locked_backfill("operations.image_backfill", lambda db: ImageBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(bind=True, name="tmdb.trailer_backfill")
def trailer_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import TrailerBackfillService

    result = _locked_backfill("tmdb.trailer_backfill", lambda db: TrailerBackfillService(db).run(batch_size))
    return (
        _continue(self, result, batch_size=batch_size, countdown=5)
        if continuous
        else result
    )


@celery_app.task(bind=True, name="ratings.imdb_backfill")
def imdb_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import IMDbBackfillService

    result = _locked_backfill("ratings.imdb_backfill", lambda db: IMDbBackfillService(db).run(batch_size))
    return (
        _continue(self, result, batch_size=batch_size, countdown=5)
        if continuous
        else result
    )


@celery_app.task(bind=True, name="operations.ott_backfill")
def ott_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import OttQueueBackfillService

    result = _locked_backfill("operations.ott_eligibility_backfill", lambda db: OttQueueBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(name="repair.movie")
def repair_movie(movie_id: int):
    from app.services.backfill import SingleMovieRepairService

    def run(db):
        state = (
            db.query(OperationState)
            .filter_by(name=f"on_demand_repair:{movie_id}")
            .first()
        )
        if not state:
            state = OperationState(name=f"on_demand_repair:{movie_id}", total_count=1)
            db.add(state)
        state.status = "RUNNING"
        db.commit()
        try:
            result = SingleMovieRepairService(db).repair(movie_id)
            state = (
                db.query(OperationState)
                .filter_by(name=f"on_demand_repair:{movie_id}")
                .first()
            )
            state.status = "COMPLETE"
            state.processed_count = 1
            state.completed_at = datetime.now(timezone.utc)
            state.last_success_at = datetime.now(timezone.utc)
            state.last_error = None
            db.commit()
            return result
        except Exception as exc:
            db.rollback()
            state = (
                db.query(OperationState)
                .filter_by(name=f"on_demand_repair:{movie_id}")
                .first()
            )
            state.status = "FAILED"
            state.last_failure_at = datetime.now(timezone.utc)
            state.last_error = sanitize_error(exc)
            db.commit()
            raise

    return _run(run)


@celery_app.task(
    name="operations.ott_verification",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_verification():
    def run(db):
        service = OttResearchService(db)
        transitions = service.transition_release_states()
        due = (
            db.query(OttAvailability)
            .filter(
                OttAvailability.manually_verified.is_(False),
                (OttAvailability.last_checked.is_(None))
                | (
                    OttAvailability.last_checked
                    < datetime.now(timezone.utc) - timedelta(days=30)
                ),
            )
            .order_by(OttAvailability.id)
            .limit(100)
            .all()
        )
        queued = 0
        for availability in due:
            queued += int(service.queue_movie(availability.movie_id))
        db.commit()
        return {"queued": queued, "transitions": transitions}

    return _logged_automated("OTT_VERIFICATION", "scheduled:ott_verification", lambda db, _run_id: run(db))


@celery_app.task(
    name="sources.ottplay_sync",
    autoretry_for=(),
)
def ottplay_source_sync():
    from app.services.ott_source_sync import OttSourceSyncService

    return _run(lambda db: OttSourceSyncService(db, "ottplay").sync())


@celery_app.task(
    name="sources.justwatch_refresh",
    autoretry_for=(),
)
def justwatch_source_refresh():
    """An adapter failure is recorded but never fails the canonical OTT pipeline."""
    from app.services.ott_source_sync import OttSourceSyncService

    return _run(lambda db: OttSourceSyncService(db, "justwatch").sync())


@celery_app.task(name="operations.notifications")
def notifications():
    def run(db):
        from app.services.notification_service import NotificationService

        open_issues = (
            db.query(DataQualityIssue)
            .filter(
                DataQualityIssue.resolved_at.is_(None),
                DataQualityIssue.severity.in_(["high", "critical"]),
            )
            .count()
        )
        failed = db.query(OttEvidence).filter_by(status="FAILED").count()
        return NotificationService(db).notify(
            f"Daily health summary: {open_issues} serious data issues; {failed} failed OTT research items",
            "info",
            "daily-health-summary",
            1380,
        )

    return _run(run)


@celery_app.task(
    name="operations.movie_requests",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def movie_requests():
    from app.services.movie_requests import MovieRequestAutomationService

    return _run(lambda db: MovieRequestAutomationService(db).maintain())


@celery_app.task(name="operations.cleanup")
def cleanup():
    def run(db):
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        notifications_deleted = (
            db.query(NotificationLog)
            .filter(NotificationLog.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        issues_deleted = (
            db.query(DataQualityIssue)
            .filter(
                DataQualityIssue.resolved_at.is_not(None),
                DataQualityIssue.resolved_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"notifications": notifications_deleted, "issues": issues_deleted}

    return _run(run)


def _window_tier(movie, today):
    """0=recent window, 1=mid window, 2=older. None date counts as older."""
    if not movie or not movie.theatrical_release_date:
        return 2
    high_cutoff = today - timedelta(days=settings.OTT_RESEARCH_HIGH_PRIORITY_DAYS)
    medium_cutoff = today - timedelta(days=settings.OTT_RESEARCH_MEDIUM_PRIORITY_DAYS)
    if movie.theatrical_release_date >= high_cutoff:
        return 0
    if movie.theatrical_release_date >= medium_cutoff:
        return 1
    return 2


def _ott_research_batch(db, research_run_id: str | None = None, limit: int | None = None):
    provider = configured_ott_provider()
    service = OttResearchService(db, settings.OTT_CONFIRMATION_THRESHOLD)
    if not getattr(provider, "configured", False):
        return {"configured": False, "processed": 0, "complete": False}
    now = datetime.now(timezone.utc)
    usage = ResearchUsageService(db)
    daily = usage.daily_snapshot(now)
    monthly = (
        usage.monthly_snapshot(now) if getattr(provider, "is_tavily", False) else None
    )
    available_slots = daily["remaining"]
    if limit is not None:
        available_slots = min(limit, available_slots)
    if monthly is not None and monthly["remaining"] <= 0:
        return {
            "configured": True,
            "processed": 0,
            "queries": 0,
            "complete": False,
            "stopped": "monthly_free_budget_exhausted",
            "daily": daily,
            "monthly": monthly,
        }
    if available_slots <= 0:
        return {
            "configured": True,
            "processed": 0,
            "queries": 0,
            "complete": False,
            "stopped": "daily_movie_limit_reached",
            "daily": daily,
            "monthly": monthly,
        }
    requested = exists(
        select(MovieRequest.id).where(
            func.lower(MovieRequest.movie_name) == func.lower(Movie.title),
            MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"]),
        )
    )
    today = site_date(now)
    high_cutoff = today - timedelta(days=settings.OTT_RESEARCH_HIGH_PRIORITY_DAYS)
    medium_cutoff = today - timedelta(days=settings.OTT_RESEARCH_MEDIUM_PRIORITY_DAYS)
    language_rank = case(
        (Movie.original_language == "ml", 0),
        (Movie.original_language == "ta", 1),
        (Movie.original_language == "te", 2),
        (Movie.original_language == "hi", 3),
        (Movie.original_language == "kn", 4),
        else_=5,
    )
    release_window_rank = case(
        (
            Movie.theatrical_release_date.isnot(None)
            & (Movie.theatrical_release_date >= high_cutoff),
            0,
        ),
        (
            Movie.theatrical_release_date.isnot(None)
            & (Movie.theatrical_release_date >= medium_cutoff),
            1,
        ),
        else_=2,
    )
    queue = (
        db.query(OttEvidence)
        .join(Movie, Movie.id == OttEvidence.movie_id)
        .filter(
            Movie.ott_research_eligibility == "ELIGIBLE",
            Movie.original_language.in_(INDIAN_ORIGINAL_LANGUAGE_CODES),
            OttEvidence.source_url.is_(None),
            OttEvidence.status.in_(
                ["UNKNOWN", "QUEUED", "POSSIBLE", "NOT_FOUND", "CONFLICTING", "FAILED"]
            ),
            OttEvidence.next_check <= now,
        )
        .order_by(
            case((requested, 0), else_=1),
            release_window_rank,
            language_rank,
            Movie.theatrical_release_date.desc(),
            Movie.popularity.desc(),
            OttEvidence.next_check,
        )
        .limit(100)
        .all()
    )
    if limit is not None and limit >= 5:
        high_slots = min(
            max(1, int(limit * 0.7)) if limit >= 5 else limit,
            available_slots,
        )
        medium_slots = available_slots - high_slots
        pool_movies = {
            m.id: m
            for m in db.query(Movie).filter(Movie.id.in_([i.movie_id for i in queue])).all()
        }

        def tier(item):
            movie = pool_movies.get(item.movie_id)
            return _window_tier(movie, today) if movie else 2

        high = [item for item in queue if tier(item) == 0][:high_slots]
        medium = [item for item in queue if tier(item) == 1][:medium_slots]
        older = [item for item in queue if tier(item) == 2]
        queue = high + medium + older[: available_slots - len(high) - len(medium)]

    if not queue:
        return {
            "configured": True,
            "processed": 0,
            "queries": 0,
            "complete": True,
            "daily": daily,
            "monthly": monthly,
            "limit": limit,
        }
    processed = 0
    queries_used = 0
    for item in queue:
        movie = db.get(Movie, item.movie_id)
        _, eligibility, _ = ReleaseStatusService(db).classify_movie(movie, now=now)
        if eligibility.code != "ELIGIBLE":
            db.commit()
            continue
        if (
            getattr(provider, "is_tavily", False)
            and usage.monthly_snapshot(now)["remaining"] <= 0
        ):
            break
        if not usage.reserve_daily_movie(now):
            break
        item.status = "RESEARCHING"
        item.attempts += 1
        maximum = settings.TAVILY_MAX_QUERIES_PER_MOVIE
        if getattr(provider, "is_tavily", False):
            monthly = usage.monthly_snapshot(now)
            maximum = min(maximum, monthly["remaining"])

            def reserve_query():
                return usage.reserve_tavily_query(now)
        else:
            reserve_query = None
        try:
            results = (
                provider.search(movie, max_queries=maximum, before_query=reserve_query)
                if movie and maximum > 0
                else []
            )
        except Exception as exc:
            item.status = "FAILED"
            item.last_checked = now
            item.next_check = service.next_check_for(
                item.status,
                attempts=item.attempts,
                theatrical_date=movie.theatrical_release_date if movie else None,
            )
            item.notes = sanitize_error(exc)
            processed += 1
            log.warning(
                "OTT provider failed for movie_id=%s; retry scheduled: %s",
                item.movie_id,
                item.notes,
            )
            db.commit()
            continue
        item_queries = max(0, getattr(provider, "last_query_count", 0))
        queries_used += item_queries
        if getattr(provider, "is_tavily", False) and item_queries == 0:
            item.status = "QUEUED"
            item.next_check = now + timedelta(days=1)
            db.commit()
            break
        if not results:
            item.status = "NOT_FOUND"
            item.last_checked = now
            item.next_check = service.next_check_for(
                item.status,
                attempts=item.attempts,
                theatrical_date=movie.theatrical_release_date if movie else None,
            )
            processed += 1
            continue
        evaluated = "UNKNOWN"
        for result in results[:3]:
            rank, score = source_rank(result.get("url"))
            inspected = result if result.get("inspected") else None
            if inspected is None:
                try:
                    inspected = inspect_source(movie, result)
                except Exception as exc:
                    log.info(
                        "OTT source inspection failed movie_id=%s source=%s: %s",
                        item.movie_id,
                        result.get("url"),
                        sanitize_error(exc),
                    )
            evidence_result = inspected or result
            evidence_rank = evidence_result.get("source_type") or rank
            evidence_score = evidence_result.get("confidence", score)
            parsed_date = evidence_result.get("release_date")
            try:
                parsed_date = date.fromisoformat(parsed_date) if parsed_date else None
            except (TypeError, ValueError):
                parsed_date = None
            published = evidence_result.get("published_date")
            try:
                published = date.fromisoformat(published[:10]) if published else None
            except (TypeError, ValueError):
                published = None
            evidence = service.record_evidence(
                item.movie_id,
                platform=evidence_result.get("platform"),
                release_date=parsed_date,
                source_url=evidence_result.get("url"),
                source_title=evidence_result.get("title"),
                source_published_at=published,
                summary=evidence_result.get("evidence_summary")
                or evidence_result.get("snippet"),
                confidence=evidence_score,
                source_type=evidence_rank,
                source_name=evidence_result.get("source_name"),
                country=evidence_result.get("country") or "UNKNOWN",
                inspected=bool(inspected),
                research_run_id=research_run_id,
            )
            if evidence.status == "CONFIRMED":
                evaluated = "CONFIRMED"
                break
            elif evidence.status in {"CONFLICTING", "NEEDS_REVIEW"}:
                evaluated = evidence.status
            elif evaluated == "UNKNOWN":
                evaluated = "POSSIBLE"
        item.status = evaluated
        item.last_checked = now
        item.next_check = service.next_check_for(
            item.status,
            attempts=item.attempts,
            theatrical_date=movie.theatrical_release_date if movie else None,
        )
        item.notes = (
            f"sources_discovered={min(len(results), 3)}; queries={item_queries}"
        )
        log.info(
            "OTT researched movie_id=%s status=%s sources=%s queries=%s",
            item.movie_id,
            item.status,
            min(len(results), 3),
            item_queries,
        )
        processed += 1
    db.commit()
    daily = usage.daily_snapshot(now)
    monthly = (
        usage.monthly_snapshot(now) if getattr(provider, "is_tavily", False) else None
    )
    return {
        "configured": True,
        "processed": processed,
        "queries": queries_used,
        "complete": not queue,
        "daily": daily,
        "monthly": monthly,
    }


@celery_app.task(
    name="operations.ott_research",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_research():
    from app.models.research import ResearchRun
    from app.services.research import ResearchPipelineService

    def run(db):
        service = ResearchPipelineService(db)
        parent, created = service.create_run(
            trigger_type="AUTOMATED_SCHEDULE", initiated_by="celery:beat",
            category="OTT", active_key="scheduled:ott_research",
        )
        if not created:
            return service.serialize(parent) | {"skipped": "already_running"}
        parent.status, parent.started_at = "RUNNING", datetime.now(timezone.utc)
        db.commit()
        try:
            result = _ott_research_batch(db, parent.run_id)
            parent = db.query(ResearchRun).filter_by(run_id=parent.run_id).one()
            parent.status = "COMPLETE"
            parent.evidence_created = db.query(func.count(OttEvidence.id)).filter_by(research_run_id=parent.run_id).scalar() or 0
            parent.result = "UPDATED" if parent.evidence_created else "NO_CHANGE"
            parent.database_changes = ["evidence_created"] if parent.evidence_created else []
            parent.details = {"batch": _json_safe(result)}
            parent.completed_at, parent.active_key = datetime.now(timezone.utc), None
            db.commit()
            return service.serialize(parent)
        except Exception:
            parent = db.query(ResearchRun).filter_by(run_id=parent.run_id).one()
            parent.status, parent.result, parent.active_key = "FAILED", "FAILED", None
            parent.completed_at = datetime.now(timezone.utc)
            db.commit()
            raise
    return _run(run)


@celery_app.task(
    name="operations.ott_research_scheduled",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_research_scheduled(limit: int, run_key: str):
    from app.models.research import ResearchRun
    from app.services.research import ResearchPipelineService

    def run(db):
        service = ResearchPipelineService(db)
        parent, created = service.create_run(
            trigger_type="AUTOMATED_SCHEDULE", initiated_by="celery:beat",
            category="OTT", active_key=run_key,
        )
        if not created:
            return service.serialize(parent) | {"skipped": "already_running"}
        parent.status, parent.started_at = "RUNNING", datetime.now(timezone.utc)
        db.commit()
        try:
            result = _ott_research_batch(db, parent.run_id, limit=limit)
            parent = db.query(ResearchRun).filter_by(run_id=parent.run_id).one()
            parent.status = "COMPLETE"
            parent.evidence_created = db.query(func.count(OttEvidence.id)).filter_by(research_run_id=parent.run_id).scalar() or 0
            parent.result = "UPDATED" if parent.evidence_created else "NO_CHANGE"
            parent.database_changes = ["evidence_created"] if parent.evidence_created else []
            parent.details = {"batch": _json_safe(result)}
            parent.completed_at, parent.active_key = datetime.now(timezone.utc), None
            db.commit()
            return service.serialize(parent)
        except Exception:
            parent = db.query(ResearchRun).filter_by(run_id=parent.run_id).one()
            parent.status, parent.result, parent.active_key = "FAILED", "FAILED", None
            parent.completed_at = datetime.now(timezone.utc)
            db.commit()
            raise
    return _run(run)


@celery_app.task(name="operations.ai_ott_research_movie")
def ai_ott_research_movie(movie_id: int, dry_run: bool = True):
    """Bounded, singleton AI research for one movie; intentionally unscheduled."""
    from app.models.research import ResearchRun
    from app.services.ott.ai_research import OTTResearchAgent
    from app.services.ai_research_audit import AIResearchAuditService
    from app.models.ai_research_audit import AIResearchAuditRun
    from app.services.research import ResearchPipelineService

    def run(db):
        if not dry_run and not settings.AI_RESEARCH_ENABLED:
            return {
                "movie_id": movie_id,
                "decision": "DEFERRED_PROVIDER_ERROR",
                "provider_error": "AI_RESEARCH_DISABLED",
                "evidence_created": 0,
            }
        service = ResearchPipelineService(db)
        record, created = service.create_run(
            movie_id=movie_id,
            trigger_type="ADMIN_MANUAL",
            initiated_by="celery:ai-ott-research",
            category="OTT_AI",
            active_key=f"ai-ott:{movie_id}",
        )
        if not created:
            return service.serialize(record) | {"skipped": "already_running"}
        record.status = "RUNNING"
        record.started_at = datetime.now(timezone.utc)
        db.commit()
        try:
            report = OTTResearchAgent(db).research_movie(
                movie_id,
                dry_run=dry_run,
                research_run_id=record.run_id,
            )
            payload = report.serializable()
            record = db.query(ResearchRun).filter_by(run_id=record.run_id).one()
            record.status = "COMPLETE"
            record.result = report.decision.value
            record.queries_attempted = report.queries
            record.providers_attempted = [report.provider]
            record.web_searches_attempted = len(report.queries)
            record.sources_discovered = len(report.findings)
            record.evidence_created = report.evidence_created
            record.details = {"ai_research": payload, "dry_run": dry_run}
            record.completed_at = datetime.now(timezone.utc)
            record.active_key = None
            db.commit()
            AIResearchAuditService(db).complete_run(record.run_id)
            return payload
        except Exception as exc:
            db.rollback()
            record = db.query(ResearchRun).filter_by(run_id=record.run_id).one()
            record.status = "FAILED"
            record.result = "DEFERRED_PROVIDER_ERROR"
            record.errors = [{"step": "ai_ott_research", "error": sanitize_error(exc)}]
            record.completed_at = datetime.now(timezone.utc)
            record.active_key = None
            db.commit()
            if db.get(AIResearchAuditRun, record.run_id):
                AIResearchAuditService(db).complete_run(
                    record.run_id, status="FAILED", error=exc
                )
            raise

    return _run(run)


@celery_app.task(
    name="research.movie",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=2,
)
def research_movie(run_id: str, scope: str = "full"):
    from app.services.research import ResearchPipelineService
    return _run(lambda db: ResearchPipelineService(db).execute(run_id, scope))


@celery_app.task(
    name="research.eligible_queue",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=2,
)
def research_eligible_queue(run_id: str):
    from app.services.research import ResearchPipelineService
    return _run(lambda db: ResearchPipelineService(db).execute_queue(run_id))


@celery_app.task(
    name="research.movie_request",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def research_movie_request(request_id: str):
    from app.services.research import ResearchPipelineService

    def run(db):
        service = ResearchPipelineService(db)
        record, created = service.create_request_run(request_id)
        return service.execute(record.run_id) if created else service.serialize(record)
    return _run(run)


@celery_app.task(
    name="notifications.request_discord",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def request_discord(delivery_id: int):
    from app.services.request_discord import RequestDiscordService

    return _run(lambda db: RequestDiscordService(db).deliver(delivery_id))


@celery_app.task(name="notifications.request_discord_recovery")
def request_discord_recovery():
    from app.services.request_discord import RequestDiscordService

    queued = _run(lambda db: RequestDiscordService(db).recover())
    return {"queued": len(queued), "delivery_ids": queued}


@celery_app.task(
    name="notifications.contact_request_email",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def contact_request_email(delivery_id: int):
    from app.services.contact_requests import ContactRequestNotificationService

    return _run(lambda db: ContactRequestNotificationService(db).deliver(delivery_id))


@celery_app.task(
    name="notifications.movie_request_email",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def movie_request_email(request_id: str, kind: str):
    from app.services.movie_requests import MovieRequestEmailService, MovieRequestEmailDeliveryError
    from app.models.operations import MovieRequest

    def run(db):
        # Completion is deliberately manual-only. This also neutralizes any
        # stale completion task left in the broker by an older deployment.
        if kind == "completion":
            return {
                "status": "MANUAL_REQUIRED",
                "request_id": request_id,
                "kind": kind,
            }
        item = db.query(MovieRequest).filter_by(request_id=request_id).first()
        if not item:
            return {"status": "MISSING", "request_id": request_id}
        result = MovieRequestEmailService(db).send(item, kind, respect_cooldown=False)
        if result["status"] == "FAILED":
            raise MovieRequestEmailDeliveryError("Movie request email delivery failed")
        return result

    return _run(run)


@celery_app.task(name="notifications.request_email_recovery")
def request_email_recovery():
    from app.services.contact_requests import ContactRequestNotificationService
    from app.services.movie_requests import MovieRequestEmailService

    def run(db):
        contact = ContactRequestNotificationService(db).recover()
        movie = MovieRequestEmailService(db).recover()
        return {"contact_queued": contact, "movie_queued": movie}

    return _run(run)


@celery_app.task(
    name="operations.ott_intelligence_daily",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_intelligence_daily():
    from app.services.ott.pipeline import OTTIntelligencePipeline

    return _run(lambda db: OTTIntelligencePipeline(db).run_daily())


@celery_app.task(
    name="operations.ott_intelligence_movie",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_intelligence_movie(movie_id: int):
    from app.services.ott.intelligence import OTTIntelligenceService

    return _run(lambda db: OTTIntelligenceService(db).refresh_movie(movie_id))


@celery_app.task(
    name="operations.ott_catalogue_audit",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_catalogue_audit(batch_size: int = 100):
    """Advance the priority-ordered, database-wide India OTT audit."""
    from app.services.ott.catalogue_audit import OttCatalogueAuditService

    return _run(lambda db: OttCatalogueAuditService(db).run(batch_size=batch_size))


@celery_app.task(
    name="operations.ott_intelligence_weekly",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_intelligence_weekly():
    from app.services.ott.pipeline import OTTIntelligencePipeline

    return _run(lambda db: OTTIntelligencePipeline(db).run_weekly())


@celery_app.task(
    name="operations.ott_web_research",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def ott_web_research(limit: int = 30):
    """Bounded AI/web evidence pass; technical failures remain distinguishable."""
    from app.services.ott.web_research import WebOttResearchService

    return _logged_automated(
        "WEB", "scheduled:ott_web_research",
        lambda db, run_id: WebOttResearchService(db).run(limit=limit, research_run_id=run_id),
    )


@celery_app.task(name="operations.ott_gold_set_evaluate")
def ott_gold_set_evaluate():
    from app.services.ott.gold_set import OttGoldSetService

    return _run(lambda db: OttGoldSetService(db).evaluate())


@celery_app.task(bind=True, name="operations.repair_orchestrator")
def repair_orchestrator(self):
    """Run accelerated stages sequentially so providers are never maxed concurrently."""

    def run(db):
        from app.services.backfill import (
            ImageBackfillService,
            IMDbBackfillService,
            MetadataBackfillService,
            OttQueueBackfillService,
            PersonBackfillService,
        )

        state = (
            db.query(OperationState)
            .filter_by(name="operations.repair_orchestrator")
            .first()
        )
        if not state:
            state = OperationState(
                name="operations.repair_orchestrator", status="RUNNING"
            )
            db.add(state)
            db.flush()
        stages = (
            MetadataBackfillService,
            PersonBackfillService,
            ImageBackfillService,
            IMDbBackfillService,
            OttQueueBackfillService,
        )
        if state.cursor < len(stages):
            result = stages[state.cursor](db).run()
            if result.get("complete") or result.get("configured") is False:
                state.cursor += 1
        elif state.cursor == 5:
            result = _ott_research_batch(db)
            if result.get("stopped"):
                state.status = "PAUSED"
                state.last_failure_at = datetime.now(timezone.utc)
                state.last_error = str(result["stopped"])
                db.commit()
                return {
                    "complete": True,
                    "blocked": result["stopped"],
                    "stage": state.cursor,
                    "result": result,
                }
            if result.get("complete") or result.get("configured") is False:
                state.cursor = 6
                health = db.query(OperationState).filter_by(name="data_health").first()
                if health:
                    health.cursor = 0
                    health.processed_count = 0
        elif state.cursor == 6:
            result = DataHealthService(db).scan(250)
            if result.get("cycle_complete"):
                state.cursor = 7
        else:
            state.status = "COMPLETE"
            state.completed_at = datetime.now(timezone.utc)
            db.commit()
            return {"complete": True, "stage": "complete"}
        state.status = "RUNNING"
        state.processed_count += int(result.get("processed", result.get("scanned", 0)))
        state.last_success_at = datetime.now(timezone.utc)
        db.commit()
        return {"complete": False, "stage": state.cursor, "result": result}

    result = _run(run)
    if not result["complete"]:
        self.apply_async(countdown=3)
    return result
