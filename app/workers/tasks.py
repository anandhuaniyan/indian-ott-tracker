import logging
from datetime import date, datetime, timedelta, timezone
from app.workers.celery_app import celery_app
from app.database.connection import SessionLocal
from app.services.operations import DataHealthService, OttResearchService
from app.services.image_fallback import ImageFallbackService
from app.services.ott_providers import ConfiguredSearchProvider, source_rank
from app.models.movie import Movie
from app.models.operations import DataQualityIssue, NotificationLog, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability

log = logging.getLogger(__name__)
def _run(service):
    db = SessionLocal()
    try: return service(db)
    finally: db.close()
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
@celery_app.task(name="operations.ott_research", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def ott_research():
    def run(db):
        provider = ConfiguredSearchProvider(); service = OttResearchService(db)
        queue = db.query(OttEvidence).filter(OttEvidence.status.in_(["UNKNOWN", "QUEUED", "POSSIBLE", "NOT_FOUND", "CONFLICTING", "FAILED"]), OttEvidence.next_check <= datetime.now(timezone.utc)).order_by(OttEvidence.next_check).limit(20).all()
        for item in queue:
            item.status = "RESEARCHING"; item.attempts += 1
            movie = db.get(__import__("app.models.movie", fromlist=["Movie"]).Movie, item.movie_id)
            results = provider.search(movie.title) if movie else []
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
        db.commit(); return len(queue)
    return _run(run)
