"""Audit or safely normalize Movie.original_language values."""

import argparse
import json

from app.database.connection import SessionLocal
from app.services.language_health import (
    language_health,
    normalize_stored_movie_languages,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit unambiguous aliases and case/whitespace normalization.",
    )
    args = parser.parse_args()
    db = SessionLocal()
    try:
        result = (
            normalize_stored_movie_languages(db, commit=True)
            if args.apply
            else language_health(db)
        )
        print(json.dumps(result, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
