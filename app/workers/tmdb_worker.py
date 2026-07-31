"""Worker runner for triggering TMDb synchronization routines."""

import sys
from app.services.tmdb.sync_movies import sync_latest_movies
from app.services.tmdb.bulk_importer import TMDbBulkImporter


def run_daily_sync():
    print("Starting daily TMDb sync for latest movies...")
    sync_latest_movies(max_pages=5)
    print("Daily TMDb sync finished.")


def run_bulk_import(start_year: int = 1950, end_year: int | None = None, languages: list[str] | None = None):
    print("Starting TMDB bulk import...")
    importer = TMDbBulkImporter(languages=languages, start_year=start_year, end_year=end_year)
    importer.run_import()
    print("TMDB bulk import finished.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--bulk":
        run_bulk_import()
    else:
        run_daily_sync()