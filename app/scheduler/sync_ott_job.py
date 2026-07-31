"""Daily background synchronization job for OTT Availability Tracking."""

import time
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.repositories.ott_availability_repository import OttAvailabilityRepository
from app.services.ott_availability_service import OttAvailabilityService


def run_daily_ott_sync(batch_size: int = 50) -> dict:
    """Synchronize OTT availability for released movies due for check.
    Implements search frequency rules, rate limiting, and exception resilience.
    """
    db: Session = SessionLocal()
    try:
        repo = OttAvailabilityRepository(db)
        service = OttAvailabilityService(db)

        movies_due = repo.get_movies_due_for_sync(limit=batch_size)
        print(f"[DAILY_OTT_SYNC] Starting sync batch for {len(movies_due)} movie(s)...")

        processed_count = 0
        updated_count = 0
        error_count = 0

        for movie in movies_due:
            try:
                print(f"[DAILY_OTT_SYNC] Processing movie_id={movie.id} ('{movie.title}')...")
                summary = service.sync_movie_ott(movie)
                processed_count += 1
                if summary.available:
                    updated_count += 1
                time.sleep(0.2)  # Rate limiting delay
            except Exception as e:
                error_count += 1
                print(f"[DAILY_OTT_SYNC] Error processing movie_id={movie.id}: {e}")

        print(f"[DAILY_OTT_SYNC] Sync batch finished: {processed_count} processed, {updated_count} available, {error_count} errors.")
        return {
            "processed": processed_count,
            "available": updated_count,
            "errors": error_count,
        }
    finally:
        db.close()


if __name__ == "__main__":
    run_daily_ott_sync()
