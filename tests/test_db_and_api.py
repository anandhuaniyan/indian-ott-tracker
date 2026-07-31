"""In-Memory Database & API Integration Tests for OTT Availability System."""

import sys
from datetime import date, datetime, timezone

sys.path.insert(0, r"C:\Users\anadh\Development\indian-ott-tracker")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.database.base import Base
from app.models.movie import Movie
from app.models.genre import Genre
from app.models.language import Language
from app.models.ott_availability import OttAvailability
from app.repositories.movie_repository import MovieRepository
from app.repositories.ott_availability_repository import OttAvailabilityRepository
from app.services.ott_availability_service import OttAvailabilityService
from app.schemas.movie import MovieRead


def test_full_ott_availability_lifecycle():
    print("--- Testing Full DB & Service Lifecycle (SQLite In-Memory) ---")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db: Session = TestingSessionLocal()

    try:
        # 1. Create a dummy movie
        movie = Movie(
            tmdb_id=550,
            title="Fight Club",
            release_date=date(1999, 10, 15),
            original_language="en",
        )
        db.add(movie)
        db.commit()
        db.refresh(movie)
        print(f"Created movie_id={movie.id}, title='{movie.title}'")

        # 2. Add OTT availability via repository
        ott_repo = OttAvailabilityRepository(db)
        rec1 = ott_repo.upsert_provider(
            movie_id=movie.id,
            provider="Netflix",
            country="IN",
            watch_type="subscription",
            source_type="TMDB",
            confidence=100.0,
            last_checked=datetime.now(timezone.utc),
        )
        rec2 = ott_repo.upsert_provider(
            movie_id=movie.id,
            provider="Amazon Prime Video",
            country="IN",
            watch_type="rent",
            source_type="GOOGLE_SEARCH",
            confidence=95.0,
            last_checked=datetime.now(timezone.utc),
        )
        ott_repo.save()

        # 3. Test OttAvailabilityService summary generation
        ott_service = OttAvailabilityService(db)
        summary = ott_service.get_summary(movie.id)
        assert summary.available is True
        assert len(summary.providers) == 2
        print("OttAvailabilitySummary generated:", summary.model_dump())

        # 4. Test MovieRepository eager load & Pydantic MovieRead model validation
        movie_repo = MovieRepository(db)
        db_movie = movie_repo.get_by_id(movie.id)
        movie_read = MovieRead.model_validate(db_movie)
        
        print("\n--- MovieRead JSON Output ---")
        json_output = movie_read.model_dump(mode="json")
        print(json_output["ott_availability"])
        
        assert json_output["ott_availability"]["available"] is True
        assert len(json_output["ott_availability"]["providers"]) == 2
        assert json_output["ott_availability"]["providers"][0]["name"] in ["Netflix", "Amazon Prime Video"]

        print("\nSUCCESS: All DB, Repository, Service, and Schema tests passed!")
    finally:
        db.close()


if __name__ == "__main__":
    test_full_ott_availability_lifecycle()
