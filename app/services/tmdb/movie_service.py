"""TMDb Movie API Service wrapper."""

from datetime import date

from app.services.tmdb.client import TMDbClient


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
            append_to_response="watch/providers",
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
    ) -> dict:
        """Discover movies filtered by original language and primary release year."""
        return self.client.get(
            "/discover/movie",
            **{
                "with_original_language": language,
                "primary_release_year": year,
                "sort_by": "primary_release_date.desc",
                "include_adult": False,
                "include_video": False,
                "page": page,
            },
        )