import httpx

from app.config.settings import settings


class TMDbClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self):
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            timeout=30,
        )

    def get(self, endpoint: str, **params):
        params["api_key"] = settings.TMDB_API_KEY

        response = self.client.get(endpoint, params=params)

        response.raise_for_status()

        return response.json()
