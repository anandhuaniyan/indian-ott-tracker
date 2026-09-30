"""Run the restart-safe July--December 2026 OTT integrity audit."""

import argparse
import json
import uuid

from redis import Redis

from app.config.settings import settings
from app.database.connection import SessionLocal
from app.services.ott.range_audit import OttRangeAuditService


LOCK_KEY = "locks:operations:ott_range_audit_2026_h2"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--until-complete", action="store_true")
    args = parser.parse_args()
    redis = Redis.from_url(settings.REDIS_URL)
    token = uuid.uuid4().hex
    if not redis.set(LOCK_KEY, token, nx=True, ex=12 * 60 * 60):
        print(json.dumps({"status": "SKIPPED_ACTIVE_RUN"}))
        return
    db = SessionLocal()
    try:
        while True:
            result = OttRangeAuditService(db).run(args.batch_size)
            print(json.dumps(result, default=str), flush=True)
            if result["status"] == "COMPLETE" or not args.until_complete:
                break
    finally:
        db.close()
        redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            LOCK_KEY,
            token,
        )


if __name__ == "__main__":
    main()
