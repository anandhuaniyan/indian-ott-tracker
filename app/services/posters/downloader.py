import os
from pathlib import Path

import httpx
from app.config.settings import settings


class PosterDownloader:
    """
    Downloads posters locally.

    Folder structure:

    media/
        posters/
            tmdb/
            fanart/
            imdb/
    """

    def __init__(self):
        self.base_folder = Path(settings.MEDIA_ROOT) / "posters"
        self.base_folder.mkdir(
            parents=True,
            exist_ok=True,
        )

    def download(
        self,
        url: str,
        movie_id: int,
        source: str,
    ) -> str | None:

        if not url:
            return None

        folder = self.base_folder / source

        folder.mkdir(
            parents=True,
            exist_ok=True,
        )

        extension = os.path.splitext(
            url
        )[1]

        if not extension:

            extension = ".jpg"

        filename = f"{movie_id}{extension}"

        filepath = folder / filename
        public_path = f"/media/posters/{source}/{filename}"

        if filepath.exists():

            return public_path

        try:

            response = httpx.get(
                url,
                timeout=30,
                follow_redirects=True,
            )

            if response.status_code != 200:

                return None

            if len(response.content) < 500:

                return None

            with open(
                filepath,
                "wb",
            ) as f:

                f.write(
                    response.content
                )

            return public_path

        except Exception:
            print("Poster download failed")

            return None
