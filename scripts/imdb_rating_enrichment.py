"""Run the quality-gated, checkpointed IMDb ratings enrichment."""

import argparse
import json
from datetime import datetime, timezone

from app.database.connection import SessionLocal
from app.services.imdb_rating_enrichment import IMDbDatasetIndex, IMDbRatingEnrichmentService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--quality-test-only", action="store_true")
    parser.add_argument("--skip-quality-test", action="store_true")
    parser.add_argument("--rerun-quality-test", action="store_true")
    args = parser.parse_args()
    dataset = IMDbDatasetIndex(args.dataset)
    dataset.load()
    db = SessionLocal()
    try:
        service = IMDbRatingEnrichmentService(db, dataset)
        print("COVERAGE_BEFORE:", json.dumps(service.coverage()), flush=True)
        state = service._state()
        quality = (state.details or {}).get("quality_test")
        passed = bool((state.details or {}).get("quality_gate_passed"))
        if not args.skip_quality_test and (not quality or args.rerun_quality_test):
            quality = service.run_quality_test()
            passed = quality["passed"]
            print("QUALITY_TEST:", json.dumps(quality, default=str), flush=True)
        if args.quality_test_only:
            return
        if not passed:
            print("QUALITY_GATE: FAILED", flush=True)
            return
        for queue in ("A", "B", "C"):
            for year, language, _count in service.groups(queue):
                consecutive_low = 0
                reassess = False
                while True:
                    result = service.run_group_batch(queue, year, language, args.batch_size)
                    print("BATCH_COMPLETE:", json.dumps(result, default=str), flush=True)
                    if result["processed"] == 0 or result["remaining_in_group"] == 0:
                        break
                    rate = result["vote_count_hit_rate"] if queue == "B" else result["imdb_rating_hit_rate"]
                    if rate >= 30:
                        consecutive_low = 0
                        reassess = False
                        continue
                    if rate >= 10:
                        if reassess:
                            break
                        reassess = True
                        continue
                    consecutive_low += 1
                    if consecutive_low >= 2:
                        break
        state = service._state()
        details = dict(state.details or {})
        details["coverage_after"] = service.coverage()
        details["queues_remaining"] = {
            queue: sum(count for _year, _language, count in service.groups(queue))
            for queue in ("A", "B", "C")
        }
        details.pop("halt_reason", None)
        details["run_status"] = "COMPLETE"
        state.details = details
        state.status = "IDLE"
        state.completed_at = datetime.now(timezone.utc)
        db.commit()
        print("RUN_COMPLETE:", json.dumps(details, default=str), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
