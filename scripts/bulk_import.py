"""CLI script to execute TMDB bulk movie import."""

import argparse
import os
import sys

# Ensure root directory is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.tmdb.bulk_importer import TMDbBulkImporter, SUPPORTED_LANGUAGES


def main():
    parser = argparse.ArgumentParser(description="Bulk import movies from TMDB for Indian languages.")
    parser.add_argument("--start-year", type=int, default=1950, help="Starting release year (default: 1950)")
    parser.add_argument("--end-year", type=int, default=None, help="Ending release year (default: current year)")
    parser.add_argument(
        "--languages",
        nargs="+",
        default=SUPPORTED_LANGUAGES,
        help="Space-separated list of TMDB language codes (e.g. ml ta te hi kn)",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset progress checkpoint and re-process all years/languages",
    )

    args = parser.parse_args()

    importer = TMDbBulkImporter(
        languages=args.languages,
        start_year=args.start_year,
        end_year=args.end_year,
    )

    importer.run_import(reset_checkpoint=args.reset_checkpoint)


if __name__ == "__main__":
    main()
