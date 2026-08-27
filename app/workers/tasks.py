import logging
from datetime import date, datetime, timedelta, timezone
from app.workers.celery_app import celery_app
from app.database.connection import SessionLocal
from app.services.operations import DataHealthService, OttResearchService
from app.services.image_fallback import ImageFallbackService
from app.config.settings import settings
from app.services.ott_providers import configured_ott_provider, source_rank
from app.models.movie import Movie
from app.models.operations import DataQualityIssue, NotificationLog, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability

log = logging.getLogger(__name__)
def _run(service):
    db = SessionLocal()
    try: return service(db)
    finally: db.close()


def _continue(task, result, *, batch_size=None, countdown=2):
    """Continue one checkpointed chain without creating unbounded parallel work."""
    if result.get("complete") is False and not result.get("stopped") and result.get("configured", True):
        db = SessionLocal(); state = None
        try:
            state = db.query(OperationState).filter_by(name=result.get("operation")).first()
            if state:
                state.status = "RUNNING"; db.commit()
            task.apply_async(kwargs={"batch_size": batch_size, "continuous": True}, countdown=countdown)
        except Exception as exc:
            if state:
                state.status = "FAILED"; state.last_failure_at = datetime.now(timezone.utc); state.last_error = str(exc)[:2000]; db.commit()
            raise
        finally:
            db.close()
    return result
@celery_app.task(name="operations.data_health", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def data_health(): return _run(lambda db: DataHealthService(db).scan())
@celery_app.task(name="operations.ott_queue", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def ott_queue(): return _run(lambda db: OttResearchService(db).queue_missing())
@celery_app.task(name="operations.image_recovery", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def image_recovery(): return _run(lambda db: ImageFallbackService(db).recover_batch())
@celery_app.task(name="operations.image_health", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def image_health(): return _run(lambda db: ImageFallbackService(db).scan())

@celery_app.task(name="tmdb.incremental_sync", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def tmdb_incremental_sync():
    from app.workers.tmdb_worker import run_daily_sync
    return run_daily_sync()

@celery_app.task(name="tmdb.metadata_enrichment", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def metadata_enrichment():
    def run(db):
        from app.services.movie_metadata_service import MovieMetadataService
        state = db.query(OperationState).filter_by(name="metadata_enrichment").first()
        if not state: state = OperationState(name="metadata_enrichment"); db.add(state); db.flush()
        movies = db.query(Movie).filter(Movie.id > state.cursor).order_by(Movie.id).limit(20).all()
        if not movies: state.cursor = 0; db.commit(); return {"processed": 0, "cycle_complete": True}
        service = MovieMetadataService(db)
        for movie in movies:
            service.enrich_movie(movie); state.cursor = movie.id; state.processed_count += 1
        state.last_success_at = datetime.now(timezone.utc); db.commit()
        return {"processed": len(movies), "cursor": state.cursor, "cycle_complete": False}
    return _run(run)


@celery_app.task(name="ratings.imdb_refresh", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def imdb_rating_refresh():
    from app.services.rating_provider import IMDbRatingRefreshService
    return _run(lambda db: IMDbRatingRefreshService(db).refresh())


@celery_app.task(bind=True, name="tmdb.metadata_backfill")
def metadata_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import MetadataBackfillService
    result = _run(lambda db: MetadataBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(bind=True, name="tmdb.person_backfill")
def person_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import PersonBackfillService
    result = _run(lambda db: PersonBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(bind=True, name="operations.image_backfill")
def image_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import ImageBackfillService
    result = _run(lambda db: ImageBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(bind=True, name="ratings.imdb_backfill")
def imdb_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import IMDbBackfillService
    result = _run(lambda db: IMDbBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size, countdown=5) if continuous else result


@celery_app.task(bind=True, name="operations.ott_backfill")
def ott_backfill(self, batch_size=None, continuous=True):
    from app.services.backfill import OttQueueBackfillService
    result = _run(lambda db: OttQueueBackfillService(db).run(batch_size))
    return _continue(self, result, batch_size=batch_size) if continuous else result


@celery_app.task(name="repair.movie")
def repair_movie(movie_id: int):
    from app.services.backfill import SingleMovieRepairService
    def run(db):
        state = db.query(OperationState).filter_by(name=f"on_demand_repair:{movie_id}").first()
        if not state:
            state = OperationState(name=f"on_demand_repair:{movie_id}", total_count=1)
            db.add(state)
        state.status = "RUNNING"; db.commit()
        try:
            result = SingleMovieRepairService(db).repair(movie_id)
            state = db.query(OperationState).filter_by(name=f"on_demand_repair:{movie_id}").first()
            state.status = "COMPLETE"; state.processed_count = 1; state.completed_at = datetime.now(timezone.utc); state.last_success_at = datetime.now(timezone.utc); state.last_error = None
            db.commit(); return result
        except Exception as exc:
            db.rollback(); state = db.query(OperationState).filter_by(name=f"on_demand_repair:{movie_id}").first()
            state.status = "FAILED"; state.last_failure_at = datetime.now(timezone.utc); state.last_error = str(exc)[:2000]
            db.commit(); raise
    return _run(run)

@celery_app.task(name="operations.ott_verification", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def ott_verification():
    def run(db):
        due = db.query(OttAvailability).filter((OttAvailability.last_checked.is_(None)) | (OttAvailability.last_checked < datetime.now(timezone.utc) - timedelta(days=30))).order_by(OttAvailability.id).limit(100).all()
        service = OttResearchService(db); queued = 0
        for availability in due:
            active = db.query(OttEvidence).filter(OttEvidence.movie_id == availability.movie_id, OttEvidence.status.in_(["QUEUED", "RESEARCHING"])).first()
            if not active:
                db.add(OttEvidence(movie_id=availability.movie_id, status="QUEUED", platform=availability.provider, release_date=availability.ott_release_date, next_check=datetime.now(timezone.utc), notes="scheduled canonical verification")); queued += 1
        db.commit(); return queued
    return _run(run)

@celery_app.task(name="operations.notifications")
def notifications():
    def run(db):
        from app.services.notification_service import NotificationService
        open_issues = db.query(DataQualityIssue).filter(DataQualityIssue.resolved_at.is_(None), DataQualityIssue.severity.in_(["high", "critical"])).count()
        failed = db.query(OttEvidence).filter_by(status="FAILED").count()
        return NotificationService(db).notify(f"Daily health summary: {open_issues} serious data issues; {failed} failed OTT research items", "info", "daily-health-summary", 1380)
    return _run(run)

@celery_app.task(name="operations.cleanup")
def cleanup():
    def run(db):
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        notifications_deleted = db.query(NotificationLog).filter(NotificationLog.created_at < cutoff).delete(synchronize_session=False)
        issues_deleted = db.query(DataQualityIssue).filter(DataQualityIssue.resolved_at.is_not(None), DataQualityIssue.resolved_at < cutoff).delete(synchronize_session=False)
        db.commit(); return {"notifications": notifications_deleted, "issues": issues_deleted}
    return _run(run)
def _ott_research_batch(db):
        provider = configured_ott_provider(); service = OttResearchService(db, settings.OTT_CONFIRMATION_THRESHOLD)
        if not getattr(provider, "configured", False):
            return {"configured": False, "processed": 0, "complete": False}
        queue = db.query(OttEvidence).filter(OttEvidence.status.in_(["UNKNOWN", "QUEUED", "POSSIBLE", "NOT_FOUND", "CONFLICTING", "FAILED"]), OttEvidence.next_check <= datetime.now(timezone.utc)).order_by(OttEvidence.next_check).limit(20).all()
        for item in queue:
            item.status = "RESEARCHING"; item.attempts += 1
            movie = db.get(__import__("app.models.movie", fromlist=["Movie"]).Movie, item.movie_id)
            results = provider.search(movie) if movie else []
            if not results:
                item.status = "NOT_FOUND"; item.last_checked = datetime.now(timezone.utc); item.next_check = service.next_check_for(item.status, attempts=item.attempts); continue
            for result in results[:3]:
                rank, score = source_rank(result["url"])
                parsed_date = result.get("release_date")
                try: parsed_date = date.fromisoformat(parsed_date) if parsed_date else None
                except ValueError: parsed_date = None
                service.record_evidence(item.movie_id, platform=result.get("platform"), release_date=parsed_date, source_url=result["url"], source_title=result["title"], summary=result["snippet"], confidence=score, source_rank=rank)
            item.status = "CONFIRMED" if any(source_rank(result["url"])[1] >= service.threshold and result.get("platform") for result in results[:3]) else "POSSIBLE"
            item.last_checked = datetime.now(timezone.utc); item.next_check = service.next_check_for(item.status, attempts=item.attempts)
        db.commit(); return {"configured": True, "processed": len(queue), "complete": not queue}


@celery_app.task(name="operations.ott_research", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def ott_research():
    return _run(_ott_research_batch)


@celery_app.task(bind=True, name="operations.repair_orchestrator")
def repair_orchestrator(self):
    """Run accelerated stages sequentially so providers are never maxed concurrently."""
    def run(db):
        from app.services.backfill import ImageBackfillService, IMDbBackfillService, MetadataBackfillService, OttQueueBackfillService, PersonBackfillService
        state = db.query(OperationState).filter_by(name="operations.repair_orchestrator").first()
        if not state:
            state = OperationState(name="operations.repair_orchestrator", status="RUNNING")
            db.add(state); db.flush()
        stages = (MetadataBackfillService, PersonBackfillService, ImageBackfillService, IMDbBackfillService, OttQueueBackfillService)
        if state.cursor < len(stages):
            result = stages[state.cursor](db).run()
            if result.get("complete") or result.get("configured") is False:
                state.cursor += 1
        elif state.cursor == 5:
            result = _ott_research_batch(db)
            if result.get("complete") or result.get("configured") is False:
                state.cursor = 6
                health = db.query(OperationState).filter_by(name="data_health").first()
                if health:
                    health.cursor = 0; health.processed_count = 0
        elif state.cursor == 6:
            result = DataHealthService(db).scan(250)
            if result.get("cycle_complete"):
                state.cursor = 7
        else:
            state.status = "COMPLETE"; state.completed_at = datetime.now(timezone.utc); db.commit()
            return {"complete": True, "stage": "complete"}
        state.status = "RUNNING"; state.processed_count += int(result.get("processed", result.get("scanned", 0)))
        state.last_success_at = datetime.now(timezone.utc); db.commit()
        return {"complete": False, "stage": state.cursor, "result": result}
    result = _run(run)
    if not result["complete"]:
        self.apply_async(countdown=3)
    return result
