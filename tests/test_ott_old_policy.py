"""Year-based OTT research cutoff policy (OTT_RESEARCH_MIN_YEAR)."""

from datetime import date, timedelta

from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import MovieReleaseDate
from app.models.operations import MovieRequest, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.operations import OttResearchService
from app.services.release_status import ReleaseStatusService, site_date


def _movie(database, tmdb_id: int, title: str, language: str = "ml") -> Movie:
    movie = Movie(tmdb_id=tmdb_id, title=title, original_language=language)
    database.add(movie)
    database.flush()
    return movie


def _theatrical(database, movie: Movie, release_date: date) -> None:
    database.add(
        MovieReleaseDate(
            movie_id=movie.id,
            country="IN",
            release_date=release_date,
            release_type="3",
        )
    )


def _request(database, movie: Movie, year: int) -> None:
    database.add(
        MovieRequest(
            request_id=f"req-{movie.tmdb_id}",
            movie_name=movie.title,
            email="user@example.com",
            release_year=year,
            status="PENDING",
        )
    )


def test_min_year_setting_default_is_2000():
    assert settings.OTT_RESEARCH_MIN_YEAR == 2000


def test_pre_cutoff_movie_is_too_old_even_when_user_requested(database):
    movie = _movie(database, 3100, "Old Classic Requested")
    _theatrical(database, movie, date(1999, 6, 15))
    _request(database, movie, 1999)
    database.commit()

    _, eligibility, _ = ReleaseStatusService(database).classify_movie(movie)

    assert movie.ott_research_eligibility == "TOO_OLD"
    assert eligibility.code == "TOO_OLD"
    assert eligibility.priority == "VERY_LOW"
    assert eligibility.next_eligible_at is None


def test_pre_cutoff_movie_is_too_old_without_request(database):
    movie = _movie(database, 3101, "Old Classic")
    _theatrical(database, movie, date(1998, 2, 10))
    database.commit()

    ReleaseStatusService(database).classify_movie(movie)

    assert movie.ott_research_eligibility == "TOO_OLD"


def test_pre_cutoff_movie_with_partial_platform_row_stays_too_old(database):
    movie = _movie(database, 3102, "Old Partial Platform")
    _theatrical(database, movie, date(1995, 9, 1))
    database.add(
        OttAvailability(
            movie_id=movie.id,
            provider="Prime Video",
            status="available",
            confidence=60,
            verification_status="UNKNOWN",
        )
    )
    database.commit()

    _, eligibility, _ = ReleaseStatusService(database).classify_movie(movie)

    assert eligibility.code == "TOO_OLD"


def test_cutoff_boundary_exact_year_is_not_blocked_by_year_guard(database):
    movie = _movie(database, 3103, "Boundary Classic Requested")
    _theatrical(database, movie, date(2000, 3, 1))
    _request(database, movie, 2000)
    database.commit()

    _, eligibility, _ = ReleaseStatusService(database).classify_movie(movie)

    assert eligibility.code == "ELIGIBLE"


def test_evidence_checkpoint_synced_to_too_old_for_old_movie(database):
    movie = _movie(database, 3104, "Old Checkpoint Sync")
    _theatrical(database, movie, date(1997, 7, 7))
    database.add(OttEvidence(movie_id=movie.id, status="POSSIBLE", next_check=site_date()))
    database.commit()

    ReleaseStatusService(database).classify_movie(movie)

    latest = (
        database.query(OttEvidence)
        .filter_by(movie_id=movie.id, source_url=None)
        .one()
    )
    assert latest.status == "TOO_OLD"
    assert latest.next_check is None
    assert "eligibility=TOO_OLD" in (latest.notes or "")


def test_queue_missing_never_researches_old_movies(database):
    today = site_date()
    fixture_ott = database.query(OttAvailability).filter_by(movie_id=1).one()
    fixture_ott.ott_release_date = today
    fixture_ott.verification_status = "CONFIRMED"
    fixture_ott.status = "released"
    old = _movie(database, 3105, "Old Skip")
    _theatrical(database, old, date(1996, 5, 5))
    recent = _movie(database, 3106, "Recent Queue")
    _theatrical(database, recent, today - timedelta(days=30))
    database.commit()

    assert OttResearchService(database).queue_missing(100) == 1

    old_evidence = database.query(OttEvidence).filter_by(movie_id=old.id).count()
    recent_evidence = database.query(OttEvidence).filter_by(movie_id=recent.id).count()
    assert old_evidence == 0
    assert recent_evidence == 1