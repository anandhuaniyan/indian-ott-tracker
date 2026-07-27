from datetime import date

from app.services.tmdb.client import TMDbClient


class TMDbMovieService:
    def __init__(self):
        self.client = TMDbClient()

    def get_movie(self, movie_id: int):
        return self.client.get(f"/movie/{movie_id}")

    def search_movie(self, query: str):
        return self.client.get(
            "/search/movie",
            query=query,
            include_adult=False,
        )

    def discover_indian_movies(self, page: int = 1):
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