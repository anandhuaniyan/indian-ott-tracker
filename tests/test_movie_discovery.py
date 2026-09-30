from datetime import date, datetime, timedelta, timezone

from app.models.discovery import MovieDiscoveryCandidate, MovieDiscoveryRun
from app.models.movie import Movie
from app.models.operations import OperationState
from app.services.movie_discovery import (
    LANGUAGES,
    UPCOMING_LANGUAGES,
    MovieDiscoveryService,
    next_regular_discovery,
)
from app.workers.celery_app import celery_app


class FakeTMDB:
    def __init__(self):
        self.calls = []

    def discover_movies_by_language_and_date_range(
        self, language, start, end, page=1, watch_region=None, minimum_vote_count=0
    ):
        self.calls.append((language, start, end, page, watch_region, minimum_vote_count))
        if language != "ml" or start.year != date.today().year or page != 1:
            return {"results": [], "total_pages": 1}
        return {
            "results": [
                {"id": 101, "title": "Example Film", "release_date": date.today().isoformat()},
                {"id": 201, "title": "Brand New Film", "release_date": date.today().isoformat()},
                {"id": 202, "title": "Example Film", "release_date": date.today().isoformat()},
            ],
            "total_pages": 1,
        }

    def get_rich_movie_details(self, tmdb_id):
        title = "Brand New Film" if tmdb_id == 201 else "Example Film"
        return {
            "id": tmdb_id,
            "title": title,
            "original_title": title,
            "original_language": "ml",
            "release_date": date.today().isoformat(),
            "adult": False,
            "external_ids": {"imdb_id": f"tt{tmdb_id:07d}"},
        }


def test_discovery_is_language_complete_identifier_first_and_idempotent(database, monkeypatch):
    fake = FakeTMDB()
    monkeypatch.setattr(
        "app.services.movie_discovery.MovieMetadataService.enrich_movie",
        lambda service, movie, payload=None: (service.db.commit(), movie)[1],
    )
    monkeypatch.setattr(
        "app.services.movie_discovery.ReleaseStatusService.classify_movie",
        lambda *_args, **_kwargs: (None, None, None),
    )
    monkeypatch.setattr(
        "app.services.movie_discovery.OttResearchService.queue_movie",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(MovieDiscoveryService, "_enqueue_enrichment", staticmethod(lambda _movie_id: None))

    start = date.today() - timedelta(days=60)
    end = date.today() + timedelta(days=180)
    first = MovieDiscoveryService(database, tmdb=fake).run(
        window_start=start,
        window_end=end,
        run_type="MANUAL",
        now=datetime.now(timezone.utc),
    )

    assert {call[0] for call in fake.calls} == set(LANGUAGES)
    assert all(call[1:3] == (start, end) for call in fake.calls)
    assert next(call for call in fake.calls if call[0] == "ml")[4:] == (None, 0)
    assert next(call for call in fake.calls if call[0] == "en")[4:] == (None, 20)
    assert first["already_existing"] == 1
    assert first["new_movies_imported"] == 1
    assert first["needs_review"] == 1
    assert database.query(Movie).filter_by(tmdb_id=201).one().title == "Brand New Film"
    review = database.query(MovieDiscoveryCandidate).filter_by(tmdb_id=202).one()
    assert review.status == "NEEDS_REVIEW"
    assert review.matched_movie_id == database.query(Movie).filter_by(tmdb_id=101).one().id
    review.status = "DUPLICATE"
    database.commit()

    second = MovieDiscoveryService(database, tmdb=fake).run(
        window_start=start,
        window_end=end,
        run_type="MANUAL",
        now=datetime.now(timezone.utc),
    )
    assert second["new_movies_imported"] == 0
    assert database.query(Movie).filter_by(tmdb_id=201).count() == 1
    assert database.query(MovieDiscoveryCandidate).filter_by(source="tmdb").count() == 3
    assert database.query(MovieDiscoveryCandidate).filter_by(tmdb_id=202).one().status == "DUPLICATE"
    assert database.query(MovieDiscoveryRun).count() == 2


def test_language_failure_is_isolated_and_reported_as_partial(database, monkeypatch):
    fake = FakeTMDB()
    original = fake.discover_movies_by_language_and_date_range
    fake.discover_movies_by_language_and_date_range = (
        lambda language, start, end, page=1, watch_region=None, minimum_vote_count=0:
        (_ for _ in ()).throw(RuntimeError("provider unavailable"))
        if language == "te"
        else original(language, start, end, page, watch_region, minimum_vote_count)
    )
    monkeypatch.setattr(
        "app.services.movie_discovery.MovieMetadataService.enrich_movie",
        lambda service, movie, payload=None: (service.db.commit(), movie)[1],
    )
    monkeypatch.setattr(
        "app.services.movie_discovery.ReleaseStatusService.classify_movie",
        lambda *_args, **_kwargs: (None, None, None),
    )
    monkeypatch.setattr(
        "app.services.movie_discovery.OttResearchService.queue_movie",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(MovieDiscoveryService, "_enqueue_enrichment", staticmethod(lambda _movie_id: None))
    monkeypatch.setattr("app.services.movie_discovery.NotificationService.notify", lambda *_args, **_kwargs: False)

    result = MovieDiscoveryService(database, tmdb=fake).run(
        window_start=date.today() - timedelta(days=60),
        window_end=date.today() + timedelta(days=180),
        run_type="MANUAL",
    )
    assert result["status"] == "PARTIAL"
    assert result["language_stats"]["te"]["status"] == "FAILED"
    assert result["language_stats"]["kn"]["status"] == "COMPLETE"
    assert "provider unavailable" in result["last_error"]


def test_discovery_schedule_uses_site_wall_clock_and_keeps_weekly_reconciliation():
    assert celery_app.conf.timezone == "Asia/Singapore"
    regular = str(celery_app.conf.beat_schedule["movie-discovery-morning-evening"]["schedule"])
    assert "8,20" in regular
    assert celery_app.conf.beat_schedule["movie-discovery-weekly-reconciliation"]["task"] == "movies.discovery_weekly"
    assert celery_app.conf.beat_schedule["ott-catalogue-audit"]["task"] == "operations.ott_catalogue_audit"
    assert celery_app.conf.beat_schedule["ott-catalogue-audit"]["kwargs"]["batch_size"] == 100
    now = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)  # 11:00 in Singapore
    next_run = next_regular_discovery(now)
    assert next_run.hour == 20
    assert next_run.utcoffset() == timedelta(hours=8)


def test_regular_discovery_scans_current_and_next_two_calendar_years(database, monkeypatch):
    service = MovieDiscoveryService(database, tmdb=FakeTMDB())
    captured = {}

    def capture_run(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(service, "run", capture_run)
    now = datetime(2026, 9, 11, 4, 0, tzinfo=timezone.utc)
    service.run_regular(now)

    assert captured["window_start"] == date(2026, 1, 1)
    assert captured["window_end"] == date(2028, 12, 31)


def test_regular_discovery_uses_prioritized_languages_year_segments_and_cursors(
    database, monkeypatch
):
    fake = FakeTMDB()
    monkeypatch.setattr(
        "app.services.movie_discovery.MovieMetadataService.enrich_movie",
        lambda service, movie, payload=None: (service.db.commit(), movie)[1],
    )
    monkeypatch.setattr(
        "app.services.movie_discovery.ReleaseStatusService.classify_movie",
        lambda *_args, **_kwargs: (None, None, None),
    )
    monkeypatch.setattr(
        "app.services.movie_discovery.OttResearchService.queue_movie",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        MovieDiscoveryService,
        "_enqueue_enrichment",
        staticmethod(lambda _movie_id: None),
    )

    current_year = date.today().year
    result = MovieDiscoveryService(database, tmdb=fake).run_regular()

    assert tuple(result["language_stats"]) == UPCOMING_LANGUAGES
    assert {call[1].year for call in fake.calls} == {
        current_year,
        current_year + 1,
        current_year + 2,
    }
    assert all(call[4:] == (None, 0) for call in fake.calls)
    assert database.query(OperationState).filter(
        OperationState.name.like("movies.discovery.upcoming.%")
    ).count() == len(UPCOMING_LANGUAGES) * 3


def test_upcoming_eligibility_preserves_discover_date_and_zero_vote_unknowns():
    item = {"id": 1327835, "release_date": "2026-11-06"}
    details = {
        "id": 1327835,
        "title": "Mookuthi Amman 2",
        "original_language": "ta",
        "release_date": "",
        "runtime": 0,
        "vote_count": 0,
        "vote_average": 0,
        "status": "Post Production",
        "adult": False,
        "video": False,
        "external_ids": {},
        "watch/providers": {},
    }

    assert MovieDiscoveryService._upcoming_rejection_reason(
        details, "ta", 2026, item
    ) is None


def test_upcoming_eligibility_rejects_unsafe_or_non_feature_candidates():
    base = {
        "id": 900,
        "title": "Candidate",
        "original_language": "ta",
        "release_date": "2027-05-01",
        "runtime": 120,
        "status": "Planned",
        "adult": False,
        "video": False,
    }
    item = {"id": 900, "release_date": "2027-05-01"}
    cases = (
        ({**base, "adult": True}, "Adult title excluded"),
        ({**base, "video": True}, "Video entry excluded"),
        ({**base, "runtime": 12}, "Short runtime excluded"),
        ({**base, "original_language": "en"}, "not ta"),
        ({**base, "id": None}, "Invalid TMDB movie metadata"),
    )
    for payload, expected in cases:
        assert expected in (
            MovieDiscoveryService._upcoming_rejection_reason(
                payload, "ta", 2027, item
            )
            or ""
        )
