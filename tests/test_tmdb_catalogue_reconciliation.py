from datetime import date

from app.models.movie import Movie
from app.models.movie_metadata import ExternalId
from app.models.ott_availability import OttAvailability
from app.services.tmdb.bulk_importer import TMDbBulkImporter
from app.workers.celery_app import celery_app
from app.workers.tasks import tmdb_catalogue_reconciliation


class FakeLockClient:
    def __init__(self):
        self.values = {}

    def set(self, key, value, *, nx, ex):
        assert nx is True
        assert ex == 12 * 60 * 60
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, _script, _keys, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


class FakeTMDB:
    def get_rich_movie_details(self, tmdb_id: int) -> dict:
        assert tmdb_id == 1327835
        return {
            "id": tmdb_id,
            "title": "Mookuthi Amman 2",
            "original_title": "மூக்குத்தி அம்மன் 2",
            "original_language": "ta",
            "release_date": "",
            "runtime": None,
            "status": "Post Production",
            "adult": False,
            "video": False,
            "popularity": 0.9923,
            "vote_average": 0,
            "vote_count": 0,
            "poster_path": "/dNNzsstJeQxnESyH0hZcLGblLMz.jpg",
            "backdrop_path": "/backdrop.jpg",
            "genres": [],
            "spoken_languages": [],
            "production_countries": [],
            "production_companies": [],
            "external_ids": {"imdb_id": "tt32909364"},
            "alternative_titles": {"titles": []},
            "credits": {"cast": [], "crew": []},
            "images": {"posters": [], "backdrops": [], "logos": []},
            "keywords": {"keywords": []},
            "release_dates": {"results": []},
            "watch/providers": {"results": {}},
            "videos": {"results": []},
        }


def test_reconciliation_preserves_discover_date_when_movie_details_omit_it(database):
    importer = TMDbBulkImporter(
        languages=["ta"],
        end_year=2028,
        end_date=date(2028, 12, 31),
        session_factory=lambda: database,
        tmdb_service=FakeTMDB(),
        operation_prefix="test.catalogue_reconciliation",
        minimum_vote_counts={"ta": 0},
    )

    result = importer._process_item(
        database,
        {
            "id": 1327835,
            "title": "Mookuthi Amman 2",
            "original_title": "மூக்குத்தி அம்மன் 2",
            "original_language": "ta",
            "release_date": "2026-11-06",
            "poster_path": "/dNNzsstJeQxnESyH0hZcLGblLMz.jpg",
            "vote_count": 0,
        },
        "ta",
        2026,
    )

    movie = database.query(Movie).filter_by(tmdb_id=1327835).one()
    assert result == "inserted"
    assert movie.title == "Mookuthi Amman 2"
    assert movie.original_language == "ta"
    assert movie.release_date == date(2026, 11, 6)
    assert movie.vote_count == 0
    assert movie.release_status_code == "UPCOMING"
    assert database.query(ExternalId).filter_by(
        movie_id=movie.id, provider="imdb", external_id="tt32909364"
    ).one()
    assert not database.query(OttAvailability).filter_by(movie_id=movie.id).first()


def test_reconciliation_allows_upcoming_identity_without_exact_date(database):
    importer = TMDbBulkImporter(
        languages=["ta"],
        end_year=2028,
        end_date=date(2028, 12, 31),
        session_factory=lambda: database,
        tmdb_service=FakeTMDB(),
        operation_prefix="test.catalogue_reconciliation",
        minimum_vote_counts={"ta": 0},
    )
    details = FakeTMDB().get_rich_movie_details(1327835)

    assert importer._eligible(details, "ta", 2026, {"id": 1327835})


def test_english_reconciliation_rejects_incomplete_released_zero_vote_entry(database):
    importer = TMDbBulkImporter(
        languages=["en"],
        end_year=2028,
        end_date=date(2028, 12, 31),
        session_factory=lambda: database,
        tmdb_service=FakeTMDB(),
        minimum_vote_counts={"en": 0},
    )
    details = {
        "id": 1752046,
        "title": "Incomplete Entry",
        "original_language": "en",
        "release_date": "2026-01-01",
        "runtime": 0,
        "status": "Released",
        "adult": False,
        "video": False,
        "vote_count": 0,
        "credits": {"cast": [], "crew": []},
        "external_ids": {},
        "production_companies": [],
    }

    assert importer._rejection_reason(details, "en", 2026) == "invalid_metadata"


def test_english_reconciliation_allows_evidenced_upcoming_zero_vote_feature(database):
    importer = TMDbBulkImporter(
        languages=["en"],
        end_year=2028,
        end_date=date(2028, 12, 31),
        session_factory=lambda: database,
        tmdb_service=FakeTMDB(),
        minimum_vote_counts={"en": 0},
    )
    details = {
        "id": 999001,
        "title": "Upcoming Feature",
        "original_language": "en",
        "release_date": "",
        "runtime": None,
        "status": "Post Production",
        "adult": False,
        "video": False,
        "vote_count": 0,
        "poster_path": "/poster.jpg",
        "overview": "A verified feature production with enough descriptive metadata.",
        "production_companies": [{"id": 1, "name": "Studio"}],
        "credits": {"cast": [{}, {}, {}], "crew": []},
        "external_ids": {},
    }

    assert importer._eligible(details, "en", 2026)


def test_six_hour_catalogue_reconciliation_is_registered():
    entry = celery_app.conf.beat_schedule["tmdb-catalogue-weekly-reconciliation"]
    assert entry["task"] == "tmdb.catalogue_reconciliation"
    assert entry["schedule"] == 21600


def test_reconciliation_can_prioritize_current_and_upcoming_years(database):
    importer = TMDbBulkImporter(
        languages=["ta"],
        start_year=2015,
        end_year=2028,
        end_date=date(2028, 12, 31),
        year_order=[2026, 2027, 2028, *range(2015, 2026)],
        session_factory=lambda: database,
        tmdb_service=FakeTMDB(),
    )

    details = importer._initial_details(database, "ta")

    assert details["current_year"] == 2026
    assert details["year_order"][:3] == [2026, 2027, 2028]


def test_scheduled_reconciliation_uses_independent_future_year_cursors(monkeypatch):
    calls = []

    class RecordingImporter:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def run_import(self, **kwargs):
            assert kwargs == {
                "restart_completed": False,
                "max_pages": 5,
                "max_new_movies_per_language": 10,
            }
            return {
                "processed": 1,
                "inserted": 0,
                "existing": 1,
                "filtered": 0,
                "duplicate_imdb": 0,
                "errors": 0,
            }

    monkeypatch.setattr(
        "app.services.tmdb.bulk_importer.TMDbBulkImporter", RecordingImporter
    )
    lock_client = FakeLockClient()
    lock_client.values["locks:tmdb:catalogue_reconciliation"] = "owner"
    monkeypatch.setattr(
        "app.workers.tasks._acquire_catalogue_reconciliation_lock",
        lambda: (lock_client, "owner"),
    )

    result = tmdb_catalogue_reconciliation.run()
    current_year = date.today().year

    assert [call["operation_prefix"] for call in calls[:3]] == [
        f"movies.catalogue_reconciliation.{current_year}",
        f"movies.catalogue_reconciliation.{current_year + 1}",
        f"movies.catalogue_reconciliation.{current_year + 2}",
    ]
    assert calls[3]["operation_prefix"] == "movies.catalogue_reconciliation.history"
    assert calls[0]["languages"] == [
        "ml", "ta", "te", "hi", "kn", "ko", "ja", "en", "fr", "es", "zh"
    ]
    assert all(call["minimum_vote_counts"] == {
        "ml": 0, "ta": 0, "te": 0, "hi": 0,
        "kn": 0, "en": 0, "ko": 0, "ja": 0,
        "fr": 0, "es": 0, "zh": 0,
    } for call in calls)
    assert calls[3]["start_year"] == 2011
    assert result["processed"] == 4
    assert lock_client.values == {}


def test_catalogue_lock_prevents_overlap(monkeypatch):
    client = FakeLockClient()
    monkeypatch.setattr(
        "app.workers.tasks.Redis.from_url", lambda *_args, **_kwargs: client
    )
    from app.workers.tasks import _acquire_catalogue_reconciliation_lock

    first = _acquire_catalogue_reconciliation_lock()
    second = _acquire_catalogue_reconciliation_lock()

    assert first is not None
    assert second is None


def test_scheduled_reconciliation_skips_while_another_run_is_active(monkeypatch):
    monkeypatch.setattr(
        "app.workers.tasks._acquire_catalogue_reconciliation_lock", lambda: None
    )

    result = tmdb_catalogue_reconciliation.run()

    assert result == {
        "status": "SKIPPED_ACTIVE_RUN",
        "processed": 0,
        "inserted": 0,
        "errors": 0,
    }


def test_historical_reconciliation_covers_2011_onward(database):
    importer = TMDbBulkImporter(
        languages=["en"],
        start_year=2011,
        end_year=2014,
        end_date=date(2014, 12, 31),
        session_factory=lambda: database,
        tmdb_service=FakeTMDB(),
    )

    assert importer.start_year == 2011
    assert importer.year_order == [2011, 2012, 2013, 2014]
