"""Run the bounded recent-Indian OTT enrichment queues in priority order."""

import argparse
import json

from app.database.connection import SessionLocal
from app.services.ott.indian_recent_enrichment import (
    IndianRecentOttEnrichmentService,
    LANGUAGES,
    YEARS,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--queue-report", action="store_true")
    parser.add_argument("--year", type=int)
    parser.add_argument("--language", choices=LANGUAGES)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        service = IndianRecentOttEnrichmentService(db)
        counts = service.queue_counts()
        print("QUEUE_COUNTS:", json.dumps(counts), flush=True)
        if args.queue_report:
            return
        groups = [
            (year, language)
            for year in YEARS
            for language in LANGUAGES
            if (args.year is None or year == args.year)
            and (args.language is None or language == args.language)
        ]
        low_yield_groups = 0
        for group_index, (year, language) in enumerate(groups):
            consecutive_low = 0
            reassess_once = False
            while True:
                result = service.run_group_batch(year, language, args.batch_size)
                next_queue = groups[group_index + 1] if group_index + 1 < len(groups) else None
                print(
                    "BATCH_COMPLETE:",
                    json.dumps(result | {"next_queue": next_queue}, default=str),
                    flush=True,
                )
                if result["processed"] == 0 or result["remaining_in_group"] == 0:
                    low_yield_groups = low_yield_groups + 1 if result["ott_date_hit_rate"] < 1 else 0
                    break
                hit_rate = result["ott_date_hit_rate"]
                if hit_rate >= 3:
                    consecutive_low = 0
                    reassess_once = False
                    continue
                if hit_rate >= 1:
                    if reassess_once:
                        break
                    reassess_once = True
                    continue
                consecutive_low += 1
                if consecutive_low >= 2:
                    low_yield_groups += 1
                    break
            # Every remaining priority queue has to demonstrate low yield before
            # the whole operation may stop; one weak language/year never blocks
            # the next higher-value group.
        state = service._state()
        details = dict(state.details or {})
        details["queue_counts_after"] = service.queue_counts()
        details["low_yield_groups"] = low_yield_groups
        state.details = details
        state.status = "IDLE"
        db.commit()
        print("RUN_COMPLETE:", json.dumps(details, default=str), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
