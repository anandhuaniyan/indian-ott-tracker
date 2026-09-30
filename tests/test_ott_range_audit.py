from datetime import date

from app.models.movie import Movie
from app.models.ott_availability import OttAvailability
from app.services.ott.range_audit import OttRangeAuditService


def test_range_audit_downgrades_placeholder_and_preserves_release_types(database):
    movie = Movie(
        tmdb_id=880001,
        title="Range Audit Example",
        release_date=date(2026, 8, 1),
        original_language="en",
    )
    database.add(movie)
    database.flush()
    placeholder = OttAvailability(
        movie_id=movie.id,
        provider="Platform TBA",
        country="IN",
        watch_type="subscription",
        ott_release_date=date(2026, 9, 1),
        status="released",
        verification_status="CONFIRMED",
        confidence=100,
    )
    rental = OttAvailability(
        movie_id=movie.id,
        provider="Amazon Video",
        country="US",
        watch_type="rent",
        ott_release_date=date(2026, 9, 2),
        status="available",
        verification_status="CONFIRMED",
        confidence=100,
    )
    database.add_all([placeholder, rental])
    database.commit()

    result = OttRangeAuditService(database).run(1000)

    assert result["status"] == "COMPLETE"
    assert placeholder.status == "unknown"
    assert placeholder.verification_status == "NEEDS_REVIEW"
    assert rental.provider == "Amazon Video"
    assert rental.watch_type == "rent"
    assert rental.ott_release_date == date(2026, 9, 2)
