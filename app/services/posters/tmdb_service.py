import os
from pathlib import Path

import httpx


class TMDBPosterService:
    """
    Downloads TMDB posters locally.

    Returns the local poster path that should be stored
    in the Movie.poster_path field.
    """

    IMAGE_BASE = "https://image.tmdb.org/t/p/original"

    def __init__(self):

        self.storage = (
            Path("storage")
            / "posters"
            / "tmdb"
        )

        self.storage.mkdir(
            parents=True,
            exist_ok=True,
        )

    def download(
        self,
        tmdb_id: int,
        poster_path: str | None,
    ) -> str | None:

        if not poster_path:
            return None

        extension = os.path.splitext(
            poster_path
        )[1]

        if not extension:
            extension = ".jpg"

        filename = f"{tmdb_id}{extension}"

        local_file = (
            self.storage
            / filename
        )

        if local_file.exists():

            return (
                f"/storage/posters/tmdb/{filename}"
            )

        url = (
            self.IMAGE_BASE
            + poster_path
        )

        try:

            response = httpx.get(
                url,
                timeout=60,
            )

            response.raise_for_status()

            local_file.write_bytes(
                response.content
            )

            return (
                f"/storage/posters/tmdb/{filename}"
            )

        except Exception as e:

            print(
                "TMDB Poster Error:",
                e,
            )

            return None