from app.database.session import SessionLocal
from app.services.ott.pipeline import OTTIntelligencePipeline


def sync_movie_ott(limit: int | None = None):
    """Compatibility entry point for the bounded evidence-first daily pipeline.

    The legacy command queried every movie and wrote current TMDB availability
    directly into a second canonical table.  This now uses the same resumable,
    quota-aware workflow as Celery and intentionally avoids a full-catalog run.
    """
    db = SessionLocal()
    try:
        result = OTTIntelligencePipeline(db).run_daily(limit=limit)
        print(result)
        return result
    finally:
        db.close()



if __name__ == "__main__":

    sync_movie_ott()
