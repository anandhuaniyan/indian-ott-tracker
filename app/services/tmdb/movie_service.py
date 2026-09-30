"""TMDb Movie API Service wrapper."""

from datetime import date

from app.services.tmdb.client import TMDbClient
from app.services.languages import normalize_language_code


class TMDbMovieService:
    """Service wrapper for TMDB Movie API endpoints."""

    def __init__(self):
        self.client = TMDbClient()

    def get_movie(self, movie_id: int) -> dict:
        """Fetch basic movie details by TMDB ID."""
        return self.client.get(f"/movie/{movie_id}")

    def get_movie_details(self, movie_id: int) -> dict:
        """Fetch full movie details with watch providers appended."""
        return self.client.get(
            f"/movie/{movie_id}",
            append_to_response="watch/providers,videos",
        )

    def get_rich_movie_details(self, movie_id: int) -> dict:
        """Fetch the movie payload required by the metadata enrichment service."""
        return self.client.get(
            f"/movie/{movie_id}",
            append_to_response="credits,external_ids,images,keywords,release_dates,alternative_titles,watch/providers,videos",
        )

    @staticmethod
    def _language_param(original_language: str | None) -> str | None:
        """Map a movie's original language to the TMDB language query value."""
        code = normalize_language_code(original_language)
        if not code:
            return None
        # TMDB returns region-specific trailers only when the language-tag
        # includes a region subtag for Indian languages (e.g. hi-IN, ta-IN).
        # English-language movies use the standard en-US tag.
        if code == "en":
            return "en-US"
        return f"{code}-IN"

    def get_movie_videos(self, movie_id: int, original_language: str | None = None) -> dict:
        """Fetch official-provider video metadata for the movie's language.

        Without an explicit language parameter TMDB only returns English videos
        for Indian regional films. Passing the movie's language with a region
        tag (e.g. hi-IN, ta-IN) surfaces the original-language trailer/teaser.
        """
        params = {}
        if original_language:
            language = self._language_param(original_language)
            if language:
                params["language"] = language
        return self.client.get(f"/movie/{movie_id}/videos", **params)

    def get_movie_external_ids(self, movie_id: int) -> dict:
        """Fetch only lawful external identifiers for a bounded recovery job."""
        return self.client.get(f"/movie/{movie_id}/external_ids")

    def get_person_details(self, person_id: int) -> dict:
        """Fetch practical person metadata and lawful external identifiers."""
        return self.client.get(
            f"/person/{person_id}",
            append_to_response="external_ids,images",
        )

    def search_movie(self, query: str) -> dict:
        """Search movies by title query."""
        return self.client.get(
            "/search/movie",
            query=query,
            include_adult=False,
        )

    def discover_indian_movies(self, page: int = 1) -> dict:
        """Discover movies with origin country 'IN' sorted by release date."""
        return self.client.get(
            "/discover/movie",
            **{
                "with_origin_country": "IN",
                "sort_by": "primary_release_date.desc",
                "primary_release_date.lte": date.today().isoformat(),
                "include_adult": False,
                "include_video": False,
                "page": page,
            },
        )

    def discover_movies_by_language_and_year(
        self,
        language: str,
        year: int,
        page: int = 1,
        watch_region: str | None = None,
        sort_by: str = "primary_release_date.desc",
        minimum_vote_count: int = 0,
        release_date_lte: date | None = None,
    ) -> dict:
        """Discover movies filtered by original language and primary release year."""
        language = normalize_language_code(language) or language
        market = (
            {
                "watch_region": watch_region,
                "with_watch_monetization_types": "flatrate|free|ads|rent|buy",
            }
            if watch_region
            else {}
        )
        return self.client.get(
            "/discover/movie",
            **{
                "with_original_language": language,
                "primary_release_year": year,
                "sort_by": sort_by,
                "include_adult": False,
                "include_video": False,
                "vote_count.gte": max(0, minimum_vote_count),
                "page": page,
                **(
                    {"primary_release_date.lte": release_date_lte.isoformat()}
                    if release_date_lte
                    else {}
                ),
                **market,
            },
        )

    def discover_movies_by_language_and_date_range(
        self,
        language: str,
        start_date: date,
        end_date: date,
        page: int = 1,
        watch_region: str | None = None,
        minimum_vote_count: int = 0,
    ) -> dict:
        """Discover movies in a bounded release window for one language."""
        language = normalize_language_code(language) or language
        market = (
            {
                "watch_region": watch_region,
                "with_watch_monetization_types": "flatrate|free|ads|rent|buy",
            }
            if watch_region
            else {}
        )
        return self.client.get(
            "/discover/movie",
            **{
                "with_original_language": language,
                "primary_release_date.gte": start_date.isoformat(),
                "primary_release_date.lte": end_date.isoformat(),
                "sort_by": "primary_release_date.desc",
                "include_adult": False,
                "include_video": False,
                "vote_count.gte": max(0, minimum_vote_count),
                "page": page,
                **market,
            },
        )
