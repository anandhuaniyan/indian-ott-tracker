"""Run the checkpointed 2020-2026 international movie expansion."""

from app.database.connection import SessionLocal
from app.services.international_expansion import InternationalExpansionService


def main() -> None:
    db = SessionLocal()
    try:
        result = InternationalExpansionService(db).run()
        for code, stats in result["languages"].items():
            print(
                f"{code}: before={stats['before']} imported={stats['imported']} "
                f"after={stats['after']} status={stats['status']}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
