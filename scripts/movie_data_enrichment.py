"""Run the resumable movie-data enrichment operation."""

import argparse
import json

from app.database.connection import SessionLocal
from app.services.movie_data_enrichment import MovieDataEnrichmentService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--retry-days", type=int, default=7)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        service = MovieDataEnrichmentService(db, retry_days=args.retry_days)
        batch_number = 1
        while True:
            print(f"BATCH {batch_number}", flush=True)
            result = service.run_batch(args.batch_size, batch_number)
            print("BATCH_COMPLETE:", json.dumps(result, default=str), flush=True)
            if not args.continuous or not result["remaining_due"]:
                break
            print("NEXT_BATCH: STARTING", flush=True)
            batch_number += 1
    finally:
        db.close()


if __name__ == "__main__":
    main()
