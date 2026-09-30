from datetime import date

from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import AlternativeTitle, MovieProductionCompany
from app.models.operations import OperationState
from app.models.ott_availability import OttAvailability
from app.services.movie_discovery import LANGUAGES
from app.services.movie_metadata_service import MovieMetadataService
from app.services.tmdb.bulk_importer import (
    MINIMUM_VOTE_COUNTS,
    OPERATION_PREFIX,
    SUPPORTED_LANGUAGES,
    TMDbBulkImporter,
)


class FakeTMDB:
    def __init__(self):
        self.calls = []

    def discover_movies_by_language_and_year(
        self,
        language,
        year,
        page=1,
        watch_region=None,
        sort_by="primary_release_date.desc",
        minimum_vote_count=0,
        release_date_lte=None,
    ):
        self.calls.append(
            (language, year, page, watch_region, sort_by, minimum_vote_count, release_date_lte)
        )
        return {
            "results": [{
                "id": 9000 + page,
                "title": f"Catalogue Film {page}",
                "release_date": f"{year}-06-01",
                "poster_path": f"/poster-{page}.jpg",
            }],
            "total_pages": 2,
        }

    def get_rich_movie_details(self, tmdb_id):
        return {
            "id": tmdb_id,
            "title": f"Catalogue Film {tmdb_id - 9000}",
            "original_title": f"Catalogue Film {tmdb_id - 9000}",
            "original_language": "ml",
            "release_date": "2015-06-01",
            "runtime": 110,
            "adult": False,
            "video": False,
            "genres": [],
            "spoken_languages": [],
            "external_ids": {"imdb_id": f"tt{tmdb_id:07d}"},
            "alternative_titles": {"titles": []},
            "credits": {"cast": [], "crew": []},
            "keywords": {"keywords": []},
            "production_companies": [],
            "production_countries": [],
            "release_dates": {"results": []},
            "images": {"posters": [], "backdrops": [], "logos": []},
            "videos": {"results": []},
            "watch/providers": {
                "results": {"IN": {"flatrate": [{"provider_name": "Netflix"}]}}
            },
        }


def test_supported_languages_and_future_schedule_cover_all_eleven():
    expected = ["ml", "ta", "te", "hi", "kn", "en", "ko", "ja", "zh", "es", "fr"]
    assert SUPPORTED_LANGUAGES == expected
    assert list(LANGUAGES) == expected
    assert settings.MOVIE_DISCOVERY_REGULAR_FUTURE_DAYS >= 365
    assert MINIMUM_VOTE_COUNTS["en"] == 20
    assert "ml" not in MINIMUM_VOTE_COUNTS


def test_database_checkpoint_resumes_without_duplicates_or_ott(database):
    fake = FakeTMDB()
    importer = TMDbBulkImporter(
        languages=["ml"],
        start_year=2015,
        end_year=2015,
        end_date=date(2015, 12, 31),
        session_factory=lambda: database,
        tmdb_service=fake,
    )

    first = importer.run_import(max_pages=1)
    assert first["languages"]["ml"]["page"] == 2
    state = database.query(OperationState).filter_by(name=f"{OPERATION_PREFIX}.ml").one()
    assert state.status == "PAUSED"
    assert state.cursor == 2
    assert database.query(Movie).filter(Movie.tmdb_id >= 9001).count() == 1
    assert database.query(OttAvailability).filter(OttAvailability.movie_id == 3).count() == 0

    resumed = TMDbBulkImporter(
        languages=["ml"],
        start_year=2015,
        end_year=2015,
        end_date=date(2015, 12, 31),
        session_factory=lambda: database,
        tmdb_service=fake,
    ).run_import()
    assert fake.calls[-1][2] == 2
    assert resumed["languages"]["ml"]["completed_years"] == [2015]
    assert database.query(Movie).filter(Movie.tmdb_id >= 9001).count() == 2
    assert database.query(OttAvailability).filter(OttAvailability.movie_id >= 3).count() == 0

    again = TMDbBulkImporter(
        languages=["ml"],
        start_year=2015,
        end_year=2015,
        end_date=date(2015, 12, 31),
        session_factory=lambda: database,
        tmdb_service=fake,
    ).run_import()
    assert again["languages"]["ml"]["after"] >= 2
    assert database.query(Movie).filter(Movie.tmdb_id >= 9001).count() == 2

    extended = TMDbBulkImporter(
        languages=["ml"],
        start_year=2015,
        end_year=2016,
        end_date=date(2016, 12, 31),
        session_factory=lambda: database,
        tmdb_service=fake,
    ).run_import()
    assert any(call[1] == 2016 for call in fake.calls)
    assert extended["languages"]["ml"]["completed_years"] == [2015, 2016]


def test_metadata_boundary_deduplicates_provider_arrays_and_clamps_title_type(database):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    service = MovieMetadataService(database, cache_artwork=False, include_ott=False)
    service._upsert_alternative_titles(movie, {
        "titles": [
            {"iso_3166_1": "IN", "title": "Alternate", "type": "x" * 180},
            {"iso_3166_1": "IN", "title": "Alternate", "type": "x" * 180},
        ]
    })
    company = {"id": 777, "name": "Studio", "origin_country": "IN"}
    service._upsert_production(movie, {
        "production_companies": [company, company],
        "production_countries": [],
    })
    database.flush()

    title = database.query(AlternativeTitle).filter_by(movie_id=movie.id).one()
    assert len(title.title_type) == 100
    assert database.query(MovieProductionCompany).filter_by(movie_id=movie.id).count() == 1
