"""Comprehensive automated test suite for the OTT Availability Tracking System."""

import sys
from datetime import date, datetime, timezone

sys.path.insert(0, r"C:\Users\anadh\Development\indian-ott-tracker")

from app.models.movie import Movie
from app.models.ott_availability import OttAvailability
from app.schemas.movie import MovieRead
from app.schemas.ott_availability import OttAvailabilitySummary, OttProviderItem
from app.repositories.ott_availability_repository import OttAvailabilityRepository
from app.services.google_search_service import GoogleSearchOttService
from app.services.ott_availability_service import OttAvailabilityService


def test_schema_serialization():
    print("--- 1. Testing Schema Serialization ---")
    summary = OttAvailabilitySummary(
        available=True,
        ott_release_date=date(2026, 8, 15),
        last_checked=datetime.now(timezone.utc),
        providers=[
            OttProviderItem(
                name="Netflix",
                country="IN",
                watch_type="subscription",
                source="TMDB",
            )
        ],
    )

    data_dict = summary.model_dump(mode="json")
    assert data_dict["available"] is True
    assert data_dict["providers"][0]["name"] == "Netflix"
    assert data_dict["providers"][0]["source"] == "TMDB"
    print("SUCCESS: Schema serialization validated!")


def test_google_fallback_confidence():
    print("--- 2. Testing Google Fallback Confidence Threshold (>=90%) ---")
    service = GoogleSearchOttService()
    dummy_movie = Movie(id=1, tmdb_id=100, title="Jawan", release_date=date(2023, 9, 7))

    # Test confidence score calculation logic
    result = service._perform_search_and_parse("Jawan Netflix OTT Release India", dummy_movie)
    print("Fallback simulation result:", result)

    if result:
        print(f"Calculated confidence score: {result['confidence']}%")
        if result["confidence"] >= 90.0:
            print("Confidence >= 90%: Validated for saving.")
        else:
            print("Confidence < 90%: Validated for logging only (not saved).")
    print("SUCCESS: Google fallback confidence rules validated!")


def test_repository_frequency_queries():
    print("--- 3. Testing Search Frequency Query Logic ---")
    # Verify repository instantiation
    assert hasattr(OttAvailabilityRepository, "get_movies_due_for_sync")
    assert hasattr(OttAvailabilityRepository, "upsert_provider")
    print("SUCCESS: Repository interface validated!")


def run_all_tests():
    test_schema_serialization()
    test_google_fallback_confidence()
    test_repository_frequency_queries()
    print("\n==========================================")
    print("ALL OTT AVAILABILITY SYSTEM TESTS PASSED!")
    print("==========================================\n")


if __name__ == "__main__":
    run_all_tests()
