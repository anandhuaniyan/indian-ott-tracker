from celery import Celery
from celery.signals import task_failure, task_success
import logging
from app.config.settings import settings
from app.core.secrets import sanitize_error

# httpx's INFO message includes complete request URLs. Provider credentials may
# be query parameters, so only warnings/errors are allowed into worker logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

celery_app = Celery("indian_ott_tracker", broker=settings.REDIS_URL, backend=settings.REDIS_URL, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True, task_default_retry_delay=60, task_serializer="json",
    result_serializer="json", timezone="UTC", enable_utc=True,
    beat_schedule={
        "tmdb-incremental-sync": {"task": "tmdb.incremental_sync", "schedule": 86400},
        "metadata-enrichment": {"task": "tmdb.metadata_enrichment", "schedule": 900},
        "imdb-id-recovery": {"task": "ratings.imdb_id_backfill", "schedule": 18000},
        "imdb-rating-refresh": {"task": "ratings.imdb_refresh", "schedule": 21600},
        "data-health": {"task": "operations.data_health", "schedule": 900},
        "image-health": {"task": "operations.image_health", "schedule": 21600},
        "image-recovery": {"task": "operations.image_recovery", "schedule": 3600},
        "release-status": {"task": "operations.release_status", "schedule": 86400},
        "ott-queue": {"task": "operations.ott_queue", "schedule": 21600},
        "ott-research": {"task": "operations.ott_research", "schedule": 1800},
        "ott-verification": {"task": "operations.ott_verification", "schedule": 86400},
        "movie-request-maintenance": {"task": "operations.movie_requests", "schedule": 1800},
        "notifications": {"task": "operations.notifications", "schedule": 86400},
        "cleanup": {"task": "operations.cleanup", "schedule": 604800},
    },
)


@task_failure.connect
def notify_task_failure(sender=None, exception=None, **_):
    """Persist and fan out important worker failures without affecting task handling."""
    from app.database.connection import SessionLocal
    from app.models.operations import OperationState
    from app.services.notification_service import NotificationService
    from datetime import datetime, timezone
    db = SessionLocal()
    try:
        name = getattr(sender, "name", "unknown-task")
        state = db.query(OperationState).filter_by(name=name).first()
        if not state: state = OperationState(name=name); db.add(state)
        safe_error = sanitize_error(exception)
        state.last_failure_at = datetime.now(timezone.utc); state.last_error = safe_error
        db.commit()
        NotificationService(db).notify(f"Background job failed: {name}: {safe_error[:500]}", "high", f"task-failure:{name}", 60)
    finally:
        db.close()


@task_success.connect
def record_task_success(sender=None, **_):
    from datetime import datetime, timezone
    from app.database.connection import SessionLocal
    from app.models.operations import OperationState
    db = SessionLocal()
    try:
        name = getattr(sender, "name", "unknown-task")
        state = db.query(OperationState).filter_by(name=name).first()
        if not state: state = OperationState(name=name); db.add(state)
        state.last_success_at = datetime.now(timezone.utc); state.last_error = None
        db.commit()
    finally:
        db.close()
