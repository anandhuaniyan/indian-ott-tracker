from celery import Celery
from app.config.settings import settings

celery_app = Celery("indian_ott_tracker", broker=settings.REDIS_URL, backend=settings.REDIS_URL, include=["app.workers.tasks"])
celery_app.conf.update(task_acks_late=True, task_default_retry_delay=60, task_serializer="json", result_serializer="json", timezone="UTC", beat_schedule={"daily-data-health": {"task": "operations.data_health", "schedule": 86400}, "daily-ott-queue": {"task": "operations.ott_queue", "schedule": 86400}, "daily-ott-research": {"task": "operations.ott_research", "schedule": 86400}, "image-recovery": {"task": "operations.image_recovery", "schedule": 21600}})
