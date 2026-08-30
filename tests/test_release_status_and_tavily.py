"""Release classification and free-only OTT research eligibility coverage."""

from datetime import date, timedelta
from types import SimpleNamespace

from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import MovieReleaseDate
from app.models.operations import OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.operations import OttResearchService
from app.services.ott_providers import TavilySearchProvider, configured_ott_provider
from app.services.release_status import (
    ReleaseStatusService,
    classify_release,
    research_status_label,
    site_date,
)
from app.workers.tasks import _ott_research_batch


def _movie(database, tmdb_id: int, title: str) -> Movie:
    movie = Movie(tmdb_id=tmdb_id, title=title, original_language="ml")
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


def test_confirmed_status_overrides_a_stale_queue_checkpoint():
    latest_queue = SimpleNamespace(status="QUEUED")

    assert research_status_label(latest_queue, "CONFIRMED") == "Confirmed"


def test_classifies_required_realistic_record_groups_without_provider_calls(database):
    # Classification uses the configured site timezone. Keep boundary ages
    # deterministic even when the test runner's UTC day differs at midnight.
    today = site_date()
    released = []
    for index, age in enumerate((10, 30, 90, 180, 365), start=1):
        movie = _movie(database, 2000 + index, f"Released Missing OTT {index}")
        _theatrical(database, movie, today - timedelta(days=age))
        released.append(movie)

    upcoming = []
    for index, days in enumerate((1, 7, 30, 90, 180), start=1):
        movie = _movie(database, 2100 + index, f"Upcoming Missing OTT {index}")
        _theatrical(database, movie, today + timedelta(days=days))
        upcoming.append(movie)

    confirmed = []
    for index in range(2):
        movie = _movie(database, 2200 + index, f"Confirmed OTT {index}")
        _theatrical(database, movie, today - timedelta(days=30))
        database.add(
            OttAvailability(
                movie_id=movie.id,
                provider="Netflix",
                ott_release_date=today - timedelta(days=2),
                status="released",
                confidence=95,
                verification_status="CONFIRMED",
            )
        )
        confirmed.append(movie)

    unknown = [
        _movie(database, 2300 + index, f"Unknown Release {index}") for index in range(2)
    ]
    direct = _movie(database, 2400, "Direct Digital Film")
    database.add(
        MovieReleaseDate(
            movie_id=direct.id,
            country="IN",
            release_date=today - timedelta(days=30),
            release_type="4",
        )
    )
    database.commit()

    ReleaseStatusService(database).classify_batch(1000)

    assert all(
        movie.release_status_code == "THEATRICALLY_RELEASED" for movie in released
    )
    assert all(movie.ott_research_eligibility == "ELIGIBLE" for movie in released)
    assert all(movie.release_status_code == "UPCOMING" for movie in upcoming)
    assert all(
        movie.ott_research_eligibility == "WAITING_RELEASE" for movie in upcoming
    )
    assert all(movie.ott_research_eligibility == "CONFIRMED" for movie in confirmed)
    assert all(movie.release_status_code == "UNKNOWN" for movie in unknown)
    assert all(movie.ott_research_eligibility == "METADATA_REPAIR" for movie in unknown)
    assert direct.release_status_code == "DIRECT_TO_OTT"
    assert direct.ott_research_eligibility == "ELIGIBLE"


def test_primary_release_prefers_original_language_india_wide_theatrical(database):
    movie = _movie(database, 2450, "Domestic First")
    database.add_all(
        [
            MovieReleaseDate(
                movie_id=movie.id,
                country="US",
                release_date=date(2026, 1, 2),
                release_type="3",
            ),
            MovieReleaseDate(
                movie_id=movie.id,
                country="IN",
                release_date=date(2026, 1, 3),
                release_type="2",
            ),
            MovieReleaseDate(
                movie_id=movie.id,
                country="IN",
                release_date=date(2026, 1, 5),
                release_type="3",
            ),
        ]
    )
    database.commit()
    assert classify_release(movie, today=date(2026, 2, 1)).theatrical_date == date(
        2026, 1, 5
    )


def test_primary_release_uses_india_then_international_fallback(database):
    india = Movie(tmdb_id=2451, title="India Fallback", original_language="en")
    international = Movie(
        tmdb_id=2452, title="International Fallback", original_language="en"
    )
    database.add_all([india, international])
    database.flush()
    database.add_all(
        [
            MovieReleaseDate(
                movie_id=india.id,
                country="US",
                release_date=date(2026, 1, 1),
                release_type="3",
            ),
            MovieReleaseDate(
                movie_id=india.id,
                country="IN",
                release_date=date(2026, 1, 4),
                release_type="3",
            ),
            MovieReleaseDate(
                movie_id=international.id,
                country="GB",
                release_date=date(2026, 2, 3),
                release_type="3",
            ),
            MovieReleaseDate(
                movie_id=international.id,
                country="US",
                release_date=date(2026, 2, 1),
                release_type="3",
            ),
        ]
    )
    database.commit()
    assert classify_release(india, today=date(2026, 3, 1)).theatrical_date == date(
        2026, 1, 4
    )
    assert classify_release(
        international, today=date(2026, 3, 1)
    ).theatrical_date == date(2026, 2, 1)


def test_general_release_fallback_never_uses_provider_appearance_as_ott_date(database):
    movie = Movie(
        tmdb_id=2453,
        title="General Release",
        original_language="ta",
        release_date=date(2026, 1, 10),
    )
    database.add(movie)
    database.flush()
    database.add(
        OttAvailability(
            movie_id=movie.id,
            provider="Example Stream",
            status="available",
            confidence=95,
            ott_release_date=None,
        )
    )
    database.commit()
    result = classify_release(movie, today=date(2026, 2, 1))
    assert result.code == "THEATRICALLY_RELEASED"
    assert result.theatrical_date == date(2026, 1, 10)
    assert result.digital_date is None


def test_research_worker_calls_tavily_only_for_released_eligible_movies(
    database, monkeypatch
):
    today = site_date()
    # Make the shared fixture's released movie complete so it cannot enter this queue.
    fixture_ott = database.query(OttAvailability).filter_by(movie_id=1).one()
    fixture_ott.ott_release_date = today
    fixture_ott.verification_status = "CONFIRMED"
    fixture_ott.status = "released"
    released = []
    for index in range(2):
        movie = _movie(database, 2500 + index, f"Eligible Research {index}")
        _theatrical(database, movie, today - timedelta(days=30 + index))
        released.append(movie)
    upcoming = _movie(database, 2510, "Do Not Search Upcoming")
    _theatrical(database, upcoming, today + timedelta(days=30))
    unknown = _movie(database, 2511, "Do Not Search Unknown")
    confirmed = _movie(database, 2512, "Do Not Search Confirmed")
    _theatrical(database, confirmed, today - timedelta(days=30))
    database.add(
        OttAvailability(
            movie_id=confirmed.id,
            provider="Prime Video",
            ott_release_date=today,
            status="released",
            confidence=95,
            verification_status="CONFIRMED",
        )
    )
    database.commit()

    assert OttResearchService(database).queue_missing(100) == 2

    class FakeTavily:
        configured = True
        is_tavily = True
        last_query_count = 0

        def __init__(self):
            self.calls = []

        def search(self, movie, *, max_queries=None, before_query=None):
            self.last_query_count = 0
            if before_query and before_query():
                self.last_query_count = 1
                self.calls.append(movie.id)
            return []

    provider = FakeTavily()
    monkeypatch.setattr("app.workers.tasks.configured_ott_provider", lambda: provider)
    result = _ott_research_batch(database)

    assert set(provider.calls) == {movie.id for movie in released}
    assert upcoming.id not in provider.calls
    assert unknown.id not in provider.calls
    assert confirmed.id not in provider.calls
    assert result["queries"] == 2


def test_monthly_budget_stops_tavily_without_paid_fallback(database, monkeypatch):
    today = site_date()
    fixture_ott = database.query(OttAvailability).filter_by(movie_id=1).one()
    fixture_ott.ott_release_date = today
    fixture_ott.verification_status = "CONFIRMED"
    fixture_ott.status = "released"
    movies = []
    for index in range(2):
        movie = _movie(database, 2600 + index, f"Budget Candidate {index}")
        _theatrical(database, movie, today - timedelta(days=30))
        movies.append(movie)
    database.commit()
    monkeypatch.setattr(settings, "TAVILY_MONTHLY_APP_BUDGET", 1)
    OttResearchService(database).queue_missing(100)

    class FakeTavily:
        configured = True
        is_tavily = True
        last_query_count = 0

        def __init__(self):
            self.calls = []

        def search(self, movie, *, max_queries=None, before_query=None):
            self.last_query_count = 0
            if before_query and before_query():
                self.last_query_count = 1
                self.calls.append(movie.id)
            return []

    provider = FakeTavily()
    monkeypatch.setattr("app.workers.tasks.configured_ott_provider", lambda: provider)
    result = _ott_research_batch(database)
    usage = (
        database.query(OperationState)
        .filter(OperationState.name.like("tavily_usage:%"))
        .one()
    )

    assert len(provider.calls) == 1
    assert usage.processed_count == usage.total_count == 1
    assert result["monthly"]["remaining"] == 0
    assert database.query(OttEvidence).filter_by(status="QUEUED").count() >= 1


def test_tavily_uses_at_most_two_disambiguated_queries(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            content = (
                "Specific Film is streaming on Netflix"
                if len(calls) == 1
                else "Specific Film streams on Netflix from September 12, 2026"
            )
            return {
                "results": [
                    {
                        "title": "Specific Film OTT release",
                        "url": f"https://netflix.com/title/{len(calls)}",
                        "content": content,
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"]["query"])
        return Response()

    monkeypatch.setattr("app.services.ott_providers.httpx.post", fake_post)
    movie = SimpleNamespace(
        title="Specific Film",
        theatrical_release_date=date(2026, 8, 1),
        release_date=date(2026, 8, 1),
        original_language="ml",
    )
    provider = TavilySearchProvider(api_key="free-key")
    results = provider.search(movie, max_queries=2, before_query=lambda: True)

    assert len(calls) == provider.last_query_count == 2
    assert all(
        '"Specific Film"' in query and "2026" in query and "ml" in query
        for query in calls
    )
    assert results[-1]["platform"] == "Netflix"
    assert results[-1]["release_date"] == "2026-09-12"


def test_tavily_selection_does_not_fall_back_to_google(monkeypatch):
    monkeypatch.setattr(settings, "OTT_RESEARCH_PROVIDER", "tavily")
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "")
    monkeypatch.setattr(settings, "OTT_SEARCH_API_KEY", "")
    monkeypatch.setattr(
        settings, "GOOGLE_SEARCH_API_KEY", "configured-but-not-selected"
    )
    monkeypatch.setattr(settings, "GOOGLE_SEARCH_ENGINE_ID", "engine")
    provider = configured_ott_provider()
    assert isinstance(provider, TavilySearchProvider)
    assert provider.configured is False
