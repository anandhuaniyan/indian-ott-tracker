"""Conservative OTT evidence, lifecycle, and precedence rules."""

from datetime import date, timedelta
from types import SimpleNamespace

from app.models.movie import Movie
from app.models.movie_metadata import MovieReleaseDate
from app.models.operations import OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.operations import OttResearchService
from app.services.ott_providers import _matches_movie, _release_date, normalize_platform
from app.workers.tasks import _ott_research_batch


def test_platform_normalization_and_article_date_is_not_release_date():
    assert normalize_platform("Amazon Prime Video") == "Prime Video"
    assert normalize_platform("PrimeVideo") == "Prime Video"
    text = "Published August 25, 2026. The movie begins streaming on Netflix September 5, 2026."
    assert _release_date(text) == "2026-09-05"
    assert (
        _release_date("Article published August 25, 2026 about the theatrical launch.")
        is None
    )


def test_wrong_title_and_explicit_wrong_language_are_rejected():
    movie = SimpleNamespace(
        title="Hero",
        original_title="Hero",
        original_language="ml",
        theatrical_release_date=date(2026, 1, 1),
        release_date=date(2026, 1, 1),
    )
    assert not _matches_movie(movie, "Another Film (2026) will stream on Netflix")
    assert not _matches_movie(movie, "Hero is a 2026 Tamil film streaming on Netflix")
    assert _matches_movie(movie, "Hero is a 2026 Malayalam film streaming on Netflix")


def test_two_independent_reputable_sources_agree(database):
    service = OttResearchService(database, confirmation_threshold=85)
    release = date.today() + timedelta(days=7)
    first = service.record_evidence(
        2,
        platform="Amazon Prime Video",
        release_date=release,
        source_url="https://www.thehindu.com/entertainment/example",
        source_type="established_publication",
        confidence=82,
    )
    assert first.status == "POSSIBLE"
    assert (
        database.query(OttAvailability)
        .filter_by(movie_id=2, provider="Prime Video")
        .first()
        is None
    )
    second = service.record_evidence(
        2,
        platform="PrimeVideo",
        release_date=release,
        source_url="https://indianexpress.com/article/example",
        source_type="established_publication",
        confidence=82,
    )
    canonical = (
        database.query(OttAvailability)
        .filter_by(movie_id=2, provider="Prime Video")
        .one()
    )
    assert second.status == "CONFIRMED"
    assert canonical.verification_status == "CONFIRMED"
    assert canonical.status == "upcoming"
    assert canonical.ott_release_date == release


def test_single_reputable_source_plus_tmdb_provider_stays_possible(database):
    checkpoint = OttEvidence(movie_id=2, status="QUEUED")
    database.add(checkpoint)
    database.add(
        OttAvailability(
            movie_id=2,
            provider="Netflix",
            country="IN",
            source_type="tmdb",
            status="available",
            verification_status="UNKNOWN",
            ott_release_date=None,
            confidence=80,
        )
    )
    database.commit()
    evidence = OttResearchService(database).record_evidence(
        2,
        platform="Netflix",
        release_date=date.today() + timedelta(days=5),
        source_url="https://www.thehindu.com/entertainment/one-source",
        source_type="established_publication",
        confidence=82,
    )
    canonical = (
        database.query(OttAvailability).filter_by(movie_id=2, provider="Netflix").one()
    )
    assert evidence.status == "POSSIBLE"
    assert canonical.ott_release_date is None
    assert canonical.verification_status == "UNKNOWN"
    assert checkpoint.status == "POSSIBLE"


def test_uninspected_search_snippet_never_confirms(database):
    evidence = OttResearchService(database).record_evidence(
        2,
        platform="Netflix",
        release_date=date.today(),
        source_url="https://netflix.com/title/discovery-only",
        source_type="official_platform",
        confidence=95,
        inspected=False,
    )
    assert evidence.status == "POSSIBLE"
    assert (
        database.query(OttAvailability)
        .filter_by(movie_id=2, provider="Netflix")
        .first()
        is None
    )


def test_manual_verification_is_not_overwritten_by_automation(database):
    service = OttResearchService(database)
    checkpoint = OttEvidence(movie_id=2, status="QUEUED")
    database.add(checkpoint)
    database.commit()
    manual_date = date.today() + timedelta(days=4)
    manual = service.manually_verify(
        2,
        platform="Prime Video",
        release_date=manual_date,
        source_url="https://primevideo.com/detail/example",
    )
    automated = service.record_evidence(
        2,
        platform="Prime Video",
        release_date=manual_date + timedelta(days=1),
        source_url="https://netflix.com/title/example",
        source_type="official_platform",
        confidence=95,
    )
    canonical = (
        database.query(OttAvailability)
        .filter_by(movie_id=2, provider="Prime Video")
        .one()
    )
    assert manual.manually_verified is True
    assert automated.status == "CONFLICTING"
    assert canonical.manually_verified is True
    assert canonical.verification_status == "CONFIRMED"
    assert canonical.ott_release_date == manual_date
    assert checkpoint.status == "CONFLICTING"


def test_publish_prefers_existing_canonical_row_over_legacy_alias(database):
    database.add_all(
        [
            OttAvailability(
                movie_id=2,
                provider="Amazon Prime Video",
                country="IN",
                status="available",
                verification_status="UNKNOWN",
            ),
            OttAvailability(
                movie_id=2,
                provider="Prime Video",
                country="IN",
                status="available",
                verification_status="UNKNOWN",
            ),
        ]
    )
    database.commit()
    release = date.today() + timedelta(days=4)

    OttResearchService(database).manually_verify(
        2,
        platform="Prime Video",
        release_date=release,
        source_url="https://primevideo.com/detail/example",
    )

    rows = {
        row.provider: row
        for row in database.query(OttAvailability).filter_by(movie_id=2).all()
    }
    assert rows["Prime Video"].ott_release_date == release
    assert rows["Prime Video"].verification_status == "CONFIRMED"
    assert rows["Amazon Prime Video"].ott_release_date is None


def test_different_platform_release_dates_are_not_a_conflict(database):
    service = OttResearchService(database)
    first_date = date.today() - timedelta(days=7)
    second_date = date.today()
    service.record_evidence(
        2,
        platform="Prime Video",
        release_date=first_date,
        source_url="https://primevideo.com/detail/example",
        source_type="official_platform",
        confidence=95,
    )
    second = service.record_evidence(
        2,
        platform="ManoramaMAX",
        release_date=second_date,
        source_url="https://manoramamax.com/detail/example",
        source_type="official_platform",
        confidence=95,
    )

    canonical = {
        row.provider: row
        for row in database.query(OttAvailability).filter_by(movie_id=2).all()
    }
    assert second.status == "CONFIRMED"
    assert canonical["Prime Video"].ott_release_date == first_date
    assert canonical["ManoramaMAX"].ott_release_date == second_date
    assert all(row.verification_status == "CONFIRMED" for row in canonical.values())


def test_release_state_transition_is_automatic(database):
    today = date.today()
    existing = (
        database.query(OttAvailability).filter_by(movie_id=1, provider="Netflix").one()
    )
    existing.ott_release_date = today
    existing.status = "upcoming"
    existing.verification_status = "CONFIRMED"
    database.add(
        OttAvailability(
            movie_id=2,
            provider="Prime Video",
            ott_release_date=today + timedelta(days=1),
            status="released",
            verification_status="CONFIRMED",
        )
    )
    database.commit()
    result = OttResearchService(database).transition_release_states(today=today)
    assert result == {"upcoming": 1, "released": 1}
    assert (
        database.query(OttAvailability)
        .filter_by(movie_id=2, provider="Prime Video")
        .one()
        .status
        == "upcoming"
    )
    assert (
        database.query(OttAvailability)
        .filter_by(movie_id=1, provider="Netflix")
        .one()
        .status
        == "released"
    )


def test_provider_failure_preserves_queue_and_never_becomes_not_found(
    database, monkeypatch
):
    movie = Movie(tmdb_id=9001, title="Provider Failure", original_language="ml")
    database.add(movie)
    database.flush()
    database.add(
        MovieReleaseDate(
            movie_id=movie.id,
            country="IN",
            release_date=date.today() - timedelta(days=40),
            release_type="3",
        )
    )
    database.commit()
    OttResearchService(database).queue_missing(100)

    class FailedProvider:
        configured = True
        is_tavily = False
        last_query_count = 0

        def search(self, *args, **kwargs):
            raise RuntimeError("temporary provider outage token=secret")

    monkeypatch.setattr(
        "app.workers.tasks.configured_ott_provider", lambda: FailedProvider()
    )
    _ott_research_batch(database)
    queue = (
        database.query(OttEvidence).filter_by(movie_id=movie.id, source_url=None).one()
    )
    assert queue.status == "FAILED"
    assert queue.next_check is not None
    assert "secret" not in (queue.notes or "")
