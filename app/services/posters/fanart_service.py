import os

import httpx


class FanartService:

    BASE_URL = "https://webservice.fanart.tv/v3/movies"

    def __init__(self):

        self.api_key = os.getenv("FANART_API_KEY")

    def get_movie_poster(
        self,
        tmdb_id: int,
    ) -> str | None:

        if not self.api_key:
            return None

        try:

            response = httpx.get(
                f"{self.BASE_URL}/{tmdb_id}",
                params={
                    "api_key": self.api_key,
                },
                timeout=30,
            )

            response.raise_for_status()

            data = response.json()

            posters = data.get("movieposter", [])

            if not posters:
                return None

            posters.sort(
                key=lambda x: int(
                    x.get("likes", 0)
                ),
                reverse=True,
            )

            return posters[0]["url"]

        except Exception as e:

            print("Fanart Error:", e)

            return None