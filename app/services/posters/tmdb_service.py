import os
from pathlib import Path

import httpx
from app.config.settings import settings


class TMDBPosterService:
    """
    Downloads TMDB posters locally.

    Returns the local poster path that should be stored
    in the Movie.poster_path field.
    """

    IMAGE_BASE = "https://image.tmdb.org/t/p/original"

    def __init__(self):

        self.storage = Path(settings.MEDIA_ROOT) / "posters" / "tmdb"

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
                f"/media/posters/tmdb/{filename}"
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
                f"/media/posters/tmdb/{filename}"
            )

        except Exception:
            print("TMDB poster download failed")

            return None
