"""CLI script to execute TMDB bulk movie import."""

import argparse
import os
import sys

# Ensure root directory is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.tmdb.bulk_importer import TMDbBulkImporter, SUPPORTED_LANGUAGES


def main():
    parser = argparse.ArgumentParser(description="Bulk import supported movie languages from TMDB.")
    parser.add_argument("--start-year", type=int, default=2015, help="Starting release year (default: 2015)")
    parser.add_argument("--end-year", type=int, default=None, help="Ending release year (default: current year)")
    parser.add_argument(
        "--languages",
        nargs="+",
        default=SUPPORTED_LANGUAGES,
        help="Space-separated supported original-language codes",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset progress checkpoint and re-process all years/languages",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="Pause after this many pages per language")
    parser.add_argument(
        "--sample-per-language",
        type=int,
        default=None,
        help="Pause after inserting this many new movies per language",
    )

    args = parser.parse_args()

    importer = TMDbBulkImporter(
        languages=args.languages,
        start_year=args.start_year,
        end_year=args.end_year,
    )

    result = importer.run_import(
        reset_checkpoint=args.reset_checkpoint,
        max_pages=args.max_pages,
        max_new_movies_per_language=args.sample_per_language,
    )
    for code, stats in result["languages"].items():
        print(
            f"{code}: year={stats.get('current_year')} page={stats.get('page')} "
            f"processed={stats.get('processed')} inserted={stats.get('inserted')} "
            f"existing={stats.get('existing')} filtered={stats.get('filtered')}"
        )


if __name__ == "__main__":
    main()
