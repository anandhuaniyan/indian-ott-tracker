from app.services.posters.fanart_service import FanartService
from app.services.posters.tmdb_service import TMDBPosterService
from app.services.posters.downloader import PosterDownloader


class PosterService:
    """
    Poster selection service.

    Priority:

    1. TMDB
    2. Fanart
    """

    def __init__(self):

        self.tmdb = TMDBPosterService()

        self.fanart = FanartService()

        self.downloader = PosterDownloader()

    def fetch(
        self,
        tmdb_id: int,
        tmdb_poster_path: str | None,
    ) -> str | None:

        # -----------------------------
        # First choice:
        # TMDB
        # -----------------------------

        poster = self.tmdb.download(
            tmdb_id,
            tmdb_poster_path,
        )

        if poster:

            return poster

        # -----------------------------
        # Second choice:
        # Fanart
        # -----------------------------

        fanart_url = self.fanart.get_movie_poster(
            tmdb_id
        )

        if fanart_url:

            return self.downloader.download(
                fanart_url,
                tmdb_id,
                source="fanart",
            )

        # -----------------------------
        # Nothing found
        # -----------------------------

        return None
