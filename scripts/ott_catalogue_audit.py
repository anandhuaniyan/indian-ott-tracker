"""Advance or measure the resumable India OTT catalogue audit."""

import argparse
import json

from app.database.connection import SessionLocal
from app.services.ott.catalogue_audit import (
    OttCatalogueAuditService,
    ott_completeness_metrics,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--batches", type=int, default=1)
    parser.add_argument("--metrics-only", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.metrics_only:
            print(json.dumps(ott_completeness_metrics(db), default=str))
            return
        for batch_number in range(1, max(1, args.batches) + 1):
            result = OttCatalogueAuditService(db).run(batch_size=args.batch_size)
            print(json.dumps({"batch": batch_number, **result}, default=str), flush=True)
            if result["status"] in {"COMPLETE", "BLOCKED"} or result.get("stopped"):
                break
    finally:
        db.close()


if __name__ == "__main__":
    main()
