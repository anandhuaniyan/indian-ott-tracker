import logging
from app.workers.celery_app import celery_app
from app.database.connection import SessionLocal
from app.services.operations import DataHealthService, OttResearchService
from app.services.image_fallback import ImageFallbackService
from app.services.ott_providers import ConfiguredSearchProvider, source_rank
from app.services.operations import OttResearchService
from app.models.operations import OttEvidence

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
@celery_app.task(name="operations.ott_research", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def ott_research():
    def run(db):
        provider = ConfiguredSearchProvider(); service = OttResearchService(db)
        queue = db.query(OttEvidence).filter(OttEvidence.status.in_(["QUEUED", "NOT_FOUND", "FAILED"])).order_by(OttEvidence.next_check).limit(20).all()
        for item in queue:
            item.status = "RESEARCHING"; item.attempts += 1
            movie = db.get(__import__("app.models.movie", fromlist=["Movie"]).Movie, item.movie_id)
            results = provider.search(movie.title) if movie else []
            if not results:
                item.status = "NOT_FOUND"; continue
            for result in results[:3]:
                rank, score = source_rank(result["url"])
                service.record_evidence(item.movie_id, source_url=result["url"], source_title=result["title"], summary=result["snippet"], confidence=score, source_rank=rank)
        db.commit(); return len(queue)
    return _run(run)
