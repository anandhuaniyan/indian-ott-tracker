"""Persistent local cache for artwork records supplied by legitimate providers."""

from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from app.config.settings import settings
from app.models.movie_metadata import MovieImage


class ArtworkService:
    """Downloads only primary artwork; failed downloads remain explicitly unavailable."""

    def __init__(self) -> None:
        self.media_root = Path(settings.MEDIA_ROOT)

    def cache(self, image: MovieImage) -> None:
        if image.local_path or not image.original_url:
            return
        extension = Path(urlparse(image.original_url).path).suffix.lower() or ".jpg"
        folder = self.media_root / f"{image.image_type}s"
        folder.mkdir(parents=True, exist_ok=True)
        relative_path = Path(f"{image.image_type}s") / f"movie-{image.movie_id}-{image.id}{extension}"
        destination = self.media_root / relative_path
        try:
            response = httpx.get(image.original_url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            destination.write_bytes(response.content)
            with Image.open(destination) as artwork:
                artwork.verify()
            image.local_path = f"/media/{relative_path.as_posix()}"
        except (httpx.HTTPError, OSError, Image.UnidentifiedImageError):
            if destination.exists():
                destination.unlink()
