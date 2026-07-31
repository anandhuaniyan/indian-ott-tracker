"""Automated unit test for TMDB Bulk Importer System."""

import os
import sys
from datetime import date

sys.path.insert(0, r"C:\Users\anadh\Development\indian-ott-tracker")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.database.base import Base
from app.models.movie import Movie
from app.models.genre import Genre
from app.models.language import Language
from app.models.ott_availability import OttAvailability
from app.services.tmdb.bulk_importer import TMDbBulkImporter, SUPPORTED_LANGUAGES, CHECKPOINT_FILE_PATH
from app.services.tmdb.movie_service import TMDbMovieService


def test_supported_languages_config():
    print("--- 1. Testing Supported Languages Configuration ---")
    expected = ["ml", "ta", "te", "hi", "kn"]
    assert SUPPORTED_LANGUAGES == expected
    print(f"SUCCESS: Supported languages verified: {SUPPORTED_LANGUAGES}")


def test_tmdb_discover_by_language_and_year():
    print("--- 2. Testing TMDbMovieService Discover Method Signature ---")
    service = TMDbMovieService()
    assert hasattr(service, "discover_movies_by_language_and_year")
    print("SUCCESS: TMDbMovieService.discover_movies_by_language_and_year method verified!")


def test_checkpoint_logic():
    print("--- 3. Testing Checkpoint Persistence ---")
    test_checkpoint = r"C:\Users\anadh\Development\indian-ott-tracker\data\test_checkpoint.json"
    if os.path.exists(test_checkpoint):
        os.remove(test_checkpoint)

    importer = TMDbBulkImporter(checkpoint_path=test_checkpoint)
    data = importer._load_checkpoint()
    assert "completed_keys" in data
    
    data["completed_keys"].append("ml_2024")
    importer._save_checkpoint(data)

    reloaded = importer._load_checkpoint()
    assert "ml_2024" in reloaded["completed_keys"]

    if os.path.exists(test_checkpoint):
        os.remove(test_checkpoint)
    print("SUCCESS: Checkpoint load and save verified!")


def run_all_tests():
    test_supported_languages_config()
    test_tmdb_discover_by_language_and_year()
    test_checkpoint_logic()
    print("\n==========================================")
    print("ALL BULK IMPORTER TESTS PASSED!")
    print("==========================================\n")


if __name__ == "__main__":
    run_all_tests()
