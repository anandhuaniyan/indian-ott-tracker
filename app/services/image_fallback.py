"""Provider-backed artwork recovery; never scrapes search engines."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy.orm import Session
from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import MovieImage, Person
from app.services.posters.poster_service import PosterService
from app.services.operations import DataHealthService

class ImageFallbackService:
    def __init__(self, db: Session): self.db, self.poster_service = db, PosterService()
    def _valid_local(self, value):
        if not value or value.startswith(("http://", "https://")): return False
        path = Path(value)
        if not path.is_absolute(): path = Path(settings.MEDIA_ROOT) / value.lstrip("/\\")
        return path.is_file() and path.stat().st_size > 0
    def recover_movie(self, movie: Movie, image_type="poster"):
        """Use existing permitted TMDB/Fanart provider chain and record every result."""
        current = movie.poster_path if image_type == "poster" else movie.backdrop_path
        if self._valid_local(current): return {"status": "HEALTHY", "path": current}
        if image_type != "poster":
            DataHealthService(self.db)._issue(movie.id, "missing_backdrop", detail="No configured backdrop recovery provider")
            self.db.commit(); return {"status": "UNRESOLVED"}
        recovered = self.poster_service.fetch(movie.tmdb_id, movie.poster_path)
        if recovered:
            movie.poster_path = recovered
            self.db.add(MovieImage(movie_id=movie.id, image_type="poster", source="fallback", source_id=str(movie.tmdb_id), local_path=recovered, is_primary=True, downloaded_at=datetime.now(timezone.utc), last_verified_at=datetime.now(timezone.utc)))
            self.db.commit(); return {"status": "RECOVERED", "path": recovered}
        DataHealthService(self.db)._issue(movie.id, "image_unresolved", "warning", "Permitted providers returned no poster")
        self.db.commit(); return {"status": "UNRESOLVED"}
    def recover_batch(self, limit=25):
        movies = self.db.query(Movie).filter((Movie.poster_path.is_(None)) | (Movie.poster_path == "")).order_by(Movie.id).limit(limit).all()
        return [self.recover_movie(movie) for movie in movies]
