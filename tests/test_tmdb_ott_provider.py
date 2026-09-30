from types import SimpleNamespace

from app.services.ott.providers.tmdb import TMDBOTTProvider
from app.services.tmdb.client import TMDbRequestError


class _MissingMovieClient:
    def get(self, endpoint, **_params):
        raise TMDbRequestError(endpoint, 404)


class _MissingReleaseDatesClient:
    def get(self, endpoint, **_params):
        if endpoint.endswith("/watch/providers"):
            return {
                "results": {
                    "IN": {
                        "link": "https://www.themoviedb.org/movie/123/watch",
                        "flatrate": [
                            {"provider_id": 8, "provider_name": "Netflix"}
                        ],
                    }
                }
            }
        raise TMDbRequestError(endpoint, 404)


def _movie():
    return SimpleNamespace(
        tmdb_id=123,
        title="Control Movie",
        original_title="Control Movie",
        release_date=None,
        original_language="en",
    )


def test_tmdb_ott_provider_treats_missing_movie_as_empty_result():
    assert TMDBOTTProvider(client=_MissingMovieClient()).fetch_movie(_movie()) == []


def test_tmdb_ott_provider_keeps_availability_when_release_dates_are_missing():
    result = TMDBOTTProvider(client=_MissingReleaseDatesClient()).fetch_movie(_movie())

    assert len(result) == 1
    assert result[0].platform_candidate == "Netflix"
    assert result[0].fact_type == "AVAILABILITY"
