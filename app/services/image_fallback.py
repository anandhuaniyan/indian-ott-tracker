"""Validated artwork recovery using only configured lawful providers."""

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import MovieImage, Person
from app.models.operations import DataQualityIssue, OperationState
from app.services.posters.poster_service import PosterService

TMDB_IMAGE = "https://image.tmdb.org/t/p/original"
MOVIE_TYPES = ("poster", "backdrop", "logo")


class ImageFallbackService:
    def __init__(self, db: Session):
        self.db = db
        self.media_root = Path(settings.MEDIA_ROOT)
        self.poster_service = PosterService()

    def _path(self, value: str | None) -> Path | None:
        if not value or value.startswith(("http://", "https://")):
            return None
        if value.startswith("/media/"):
            return self.media_root / value.removeprefix("/media/")
        path = Path(value)
        if path.is_absolute():
            return path
        return Path.cwd() / value.lstrip("/\\")

    def validate(self, value: str | None) -> str:
        """Derive HEALTHY, MISSING or BROKEN from a local cached file."""
        path = self._path(value)
        if not value or path is None:
            return "MISSING"
        if not path.is_file() or path.stat().st_size <= 0:
            return "MISSING"
        try:
            with Image.open(path) as artwork:
                artwork.verify()
            return "HEALTHY"
        except (OSError, UnidentifiedImageError):
            return "BROKEN"

    def _issue(self, issue_type: str, detail: str, *, movie_id=None, person_id=None, severity="warning"):
        query = self.db.query(DataQualityIssue).filter_by(issue_type=issue_type, movie_id=movie_id, person_id=person_id, resolved_at=None)
        issue = query.first()
        if not issue:
            issue = DataQualityIssue(issue_type=issue_type, movie_id=movie_id, person_id=person_id, severity=severity, detail=detail)
            self.db.add(issue)
        else:
            issue.detail = detail
        return issue

    def _resolve(self, issue_types, *, movie_id=None, person_id=None):
        self.db.query(DataQualityIssue).filter(DataQualityIssue.issue_type.in_(issue_types), DataQualityIssue.movie_id == movie_id, DataQualityIssue.person_id == person_id, DataQualityIssue.resolved_at.is_(None)).update({"resolved_at": datetime.now(timezone.utc)}, synchronize_session=False)

    def _download(self, url: str, folder: str, filename: str) -> str | None:
        extension = Path(urlparse(url).path).suffix.lower()
        if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
            extension = ".jpg"
        relative = Path(folder) / f"{filename}{extension}"
        destination = self.media_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = httpx.get(url, timeout=30, follow_redirects=True)
            response.raise_for_status()
            if not response.content:
                return None
            destination.write_bytes(response.content)
            with Image.open(destination) as artwork:
                artwork.verify()
            return f"/media/{relative.as_posix()}"
        except (httpx.HTTPError, OSError, UnidentifiedImageError):
            if destination.exists():
                destination.unlink()
            return None

    def _movie_candidates(self, movie: Movie, image_type: str):
        current = movie.poster_path if image_type == "poster" else movie.backdrop_path if image_type == "backdrop" else None
        if current and current.startswith(("http://", "https://")):
            yield "configured", current
        elif current and current.startswith("/") and not current.startswith(("/media/", "/storage/")):
            yield "tmdb", f"{TMDB_IMAGE}{current}"
        images = self.db.query(MovieImage).filter_by(movie_id=movie.id, image_type=image_type).order_by(MovieImage.is_primary.desc()).all()
        order = {"tmdb": 0, "fanart": 1}
        for image in sorted(images, key=lambda item: order.get(item.source.lower(), 2)):
            if image.original_url:
                yield image.source, image.original_url

    def recover_movie(self, movie: Movie, image_type="poster"):
        if image_type not in MOVIE_TYPES:
            raise ValueError(f"Unsupported image type: {image_type}")
        current = movie.poster_path if image_type == "poster" else movie.backdrop_path if image_type == "backdrop" else None
        if self.validate(current) == "HEALTHY":
            self._resolve([f"missing_{image_type}", f"broken_{image_type}", "image_unresolved"], movie_id=movie.id)
            self.db.commit()
            return {"status": "HEALTHY", "path": current, "type": image_type}
        previous = "BROKEN" if self.validate(current) == "BROKEN" else "MISSING"
        self._issue(f"{previous.lower()}_{image_type}", f"{image_type.title()} cache is {previous.lower()}; recovery queued", movie_id=movie.id)
        self._issue("image_retrying", f"Trying permitted {image_type} providers", movie_id=movie.id)
        recovered = None
        source = "poster-chain"
        for source, url in self._movie_candidates(movie, image_type):
            recovered = self._download(url, f"{image_type}s/{source.lower()}", f"movie-{movie.id}")
            if recovered:
                break
        if not recovered and image_type == "poster":
            recovered = self.poster_service.fetch(movie.tmdb_id, movie.poster_path)
            if recovered and self.validate(recovered) != "HEALTHY":
                recovered = None
        if recovered:
            if image_type == "poster": movie.poster_path = recovered
            elif image_type == "backdrop": movie.backdrop_path = recovered
            image = self.db.query(MovieImage).filter_by(movie_id=movie.id, image_type=image_type, is_primary=True).first()
            if not image:
                image = MovieImage(movie_id=movie.id, image_type=image_type, source="fallback", source_id=f"recovered-{movie.id}-{image_type}", is_primary=True)
                self.db.add(image)
            image.local_path = recovered; image.downloaded_at = datetime.now(timezone.utc); image.last_verified_at = datetime.now(timezone.utc)
            self._resolve([f"missing_{image_type}", f"broken_{image_type}", "image_retrying", "image_unresolved"], movie_id=movie.id)
            self._issue("image_recovered", f"Recovered {image_type} through {source}", movie_id=movie.id).resolved_at = datetime.now(timezone.utc)
            self.db.commit(); return {"status": "RECOVERED", "path": recovered, "type": image_type}
        self._resolve(["image_retrying"], movie_id=movie.id)
        self._issue("image_unresolved", f"Permitted providers returned no {image_type}", movie_id=movie.id)
        self.db.commit(); return {"status": "UNRESOLVED", "type": image_type, "previous": previous}

    def recover_person(self, person: Person):
        if self.validate(person.profile_path) == "HEALTHY":
            self._resolve(["missing_profile", "broken_profile", "image_unresolved"], person_id=person.id)
            self.db.commit(); return {"status": "HEALTHY", "path": person.profile_path, "type": "profile"}
        state = self.validate(person.profile_path)
        self._issue(f"{state.lower()}_profile", f"Profile cache is {state.lower()}", person_id=person.id)
        url = person.profile_path if person.profile_path and person.profile_path.startswith("http") else f"{TMDB_IMAGE}{person.profile_path}" if person.profile_path and person.profile_path.startswith("/") else None
        recovered = self._download(url, "profiles/tmdb", f"person-{person.id}") if url else None
        if recovered:
            person.profile_path = recovered
            self._resolve(["missing_profile", "broken_profile", "image_unresolved"], person_id=person.id)
            self.db.commit(); return {"status": "RECOVERED", "path": recovered, "type": "profile"}
        self._issue("image_unresolved", "Permitted providers returned no profile image", person_id=person.id)
        self.db.commit(); return {"status": "UNRESOLVED", "type": "profile", "previous": state}

    def scan(self, batch_size=25):
        """Resume across every movie and person; reset cursors only after a full cycle."""
        results = {"movies": 0, "people": 0, "statuses": {}}
        movie_state = self.db.query(OperationState).filter_by(name="image_health_movies").first()
        if not movie_state:
            movie_state = OperationState(name="image_health_movies"); self.db.add(movie_state); self.db.flush()
        movies = self.db.query(Movie).filter(Movie.id > movie_state.cursor).order_by(Movie.id).limit(batch_size).all()
        for movie in movies:
            for image_type in MOVIE_TYPES:
                outcome = self.recover_movie(movie, image_type)
                results["statuses"][outcome["status"]] = results["statuses"].get(outcome["status"], 0) + 1
            movie_state.cursor = movie.id; movie_state.processed_count += 1; results["movies"] += 1
        if not movies:
            movie_state.cursor = 0
        person_state = self.db.query(OperationState).filter_by(name="image_health_people").first()
        if not person_state:
            person_state = OperationState(name="image_health_people"); self.db.add(person_state); self.db.flush()
        people = self.db.query(Person).filter(Person.id > person_state.cursor).order_by(Person.id).limit(batch_size).all()
        for person in people:
            outcome = self.recover_person(person)
            results["statuses"][outcome["status"]] = results["statuses"].get(outcome["status"], 0) + 1
            person_state.cursor = person.id; person_state.processed_count += 1; results["people"] += 1
        if not people:
            person_state.cursor = 0
        movie_state.last_success_at = person_state.last_success_at = datetime.now(timezone.utc)
        self.db.commit()
        return results | {"movie_cursor": movie_state.cursor, "person_cursor": person_state.cursor, "cycle_complete": not movies and not people}

    def recover_batch(self, limit=25):
        return self.scan(limit)
