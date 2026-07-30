from app.services.tmdb.client import TMDbClient


class TMDbOttService:

    def __init__(self):
        self.client = TMDbClient()


    def get_movie_watch_providers(self, tmdb_id: int):

        response = self.client.get(
            f"/movie/{tmdb_id}/watch/providers"
        )

        return response.get("results", {}).get("IN", {})