import os
from pathlib import Path

import httpx


class PosterDownloader:
    """
    Downloads posters locally.

    Folder structure:

    storage/
        posters/
            tmdb/
            fanart/
            imdb/
    """

    BASE_FOLDER = Path("storage/posters")

    def __init__(self):

        self.BASE_FOLDER.mkdir(
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

        folder = self.BASE_FOLDER / source

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

        if filepath.exists():

            return str(filepath)

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

            return str(filepath)

        except Exception as e:

            print(
                "Poster download failed:",
                e,
            )

            return None