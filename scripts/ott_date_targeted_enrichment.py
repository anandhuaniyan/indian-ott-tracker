"""Run the separate priority OTT-date enrichment operation."""

import argparse
import json

from app.database.connection import SessionLocal
from app.services.ott.targeted_date_enrichment import TargetedOttDateEnrichmentService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--trial-manifest")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        service = TargetedOttDateEnrichmentService(db)
        if args.trial_manifest:
            with open(args.trial_manifest, encoding="utf-8") as handle:
                print("QUALITY_TEST:", json.dumps(service.record_verified_trial(json.load(handle)), default=str), flush=True)
            return
        batch = None
        while True:
            result = service.run_batch(args.batch_size, batch)
            print("BATCH_COMPLETE:", json.dumps(result, default=str), flush=True)
            if not args.continuous or not result["remaining_due"]:
                break
            batch = result["batch"] + 1
    finally:
        db.close()


if __name__ == "__main__":
    main()
