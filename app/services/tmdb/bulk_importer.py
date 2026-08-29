"""TMDB Bulk Movie Importer Service with checkpointing, rate limiting, and batch database commits."""

import json
import os
from datetime import date, datetime
from slugify import slugify
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.genre import Genre
from app.models.language import Language
from app.models.movie import Movie
from app.repositories.genre_repository import GenreRepository
from app.repositories.language_repository import LanguageRepository
from app.repositories.movie_repository import MovieRepository
from app.services.tmdb.movie_service import TMDbMovieService
from app.services.trailers import TrailerService


SUPPORTED_LANGUAGES = ["ml", "ta", "te", "hi", "kn"]
CHECKPOINT_FILE_PATH = os.path.join(os.getcwd(), "data", "import_checkpoint.json")


class TMDbBulkImporter:
    """Orchestrates bulk movie importing from TMDB across years and languages."""

    def __init__(
        self,
        languages: list[str] | None = None,
        start_year: int = 1950,
        end_year: int | None = None,
        checkpoint_path: str = CHECKPOINT_FILE_PATH,
    ):
        self.languages = languages or SUPPORTED_LANGUAGES
        self.start_year = start_year
        self.end_year = end_year or date.today().year
        self.checkpoint_path = checkpoint_path
        self.tmdb_service = TMDbMovieService()
        self.stats = {
            "processed": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }

    def _load_checkpoint(self) -> dict:
        if os.path.exists(self.checkpoint_path):
            try:
                with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[BULK_IMPORTER] Warning: Could not read checkpoint file: {e}")
        return {"completed_keys": []}

    def _save_checkpoint(self, checkpoint_data: dict) -> None:
        checkpoint_directory = os.path.dirname(self.checkpoint_path)
        if checkpoint_directory:
            os.makedirs(checkpoint_directory, exist_ok=True)
        try:
            with open(self.checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(checkpoint_data, f, indent=2)
        except Exception as e:
            print(f"[BULK_IMPORTER] Warning: Could not save checkpoint file: {e}")

    def run_import(self, reset_checkpoint: bool = False) -> dict:
        """Execute the bulk import operation across configured languages and years."""
        print(f"[BULK_IMPORTER] Starting bulk import for languages={self.languages}, years={self.start_year}-{self.end_year}...")
        
        checkpoint = {"completed_keys": []} if reset_checkpoint else self._load_checkpoint()
        completed_keys = set(checkpoint.get("completed_keys", []))

        for lang in self.languages:
            for year in range(self.start_year, self.end_year + 1):
                key = f"{lang}_{year}"
                if key in completed_keys:
                    print(f"[BULK_IMPORTER] Skipping completed block {key}")
                    continue

                print(f"\n[BULK_IMPORTER] ---> Importing language='{lang}', year={year}...")
                self._import_language_year(lang, year)

                completed_keys.add(key)
                checkpoint["completed_keys"] = list(completed_keys)
                self._save_checkpoint(checkpoint)

        print("\n==========================================")
        print("BULK IMPORT COMPLETE SUMMARY")
        print(f" Total Processed : {self.stats['processed']}")
        print(f" Total Inserted  : {self.stats['inserted']}")
        print(f" Total Updated   : {self.stats['updated']}")
        print(f" Total Errors    : {self.stats['errors']}")
        print("==========================================\n")
        return self.stats

    def _import_language_year(self, lang: str, year: int) -> None:
        """Fetch and import all pages for a specific language and year."""
        db: Session = SessionLocal()
        movie_repo = MovieRepository(db)
        genre_repo = GenreRepository(db)
        lang_repo = LanguageRepository(db)

        try:
            page = 1
            total_pages = 1

            while page <= total_pages:
                try:
                    response = self.tmdb_service.discover_movies_by_language_and_year(
                        language=lang,
                        year=year,
                        page=page,
                    )

                    results = response.get("results", [])
                    total_pages = min(response.get("total_pages", 1), 500)  # TMDB limits discover page index to 500

                    if not results:
                        break

                    for item in results:
                        self.stats["processed"] += 1
                        tmdb_id = item["id"]

                        existing_movie = movie_repo.get_by_tmdb_id(tmdb_id)

                        # Fetch detailed movie metadata for runtime, full genres, status
                        details = self.tmdb_service.get_movie_details(tmdb_id)

                        rel_date = None
                        if details.get("release_date"):
                            try:
                                rel_date = date.fromisoformat(details["release_date"])
                            except ValueError:
                                rel_date = None

                        if existing_movie:
                            existing_movie.title = details.get("title") or item.get("title", "")
                            existing_movie.original_title = details.get("original_title") or item.get("original_title")
                            existing_movie.overview = details.get("overview") or item.get("overview")
                            existing_movie.release_date = rel_date
                            existing_movie.runtime_minutes = details.get("runtime")
                            existing_movie.poster_path = details.get("poster_path")
                            existing_movie.backdrop_path = details.get("backdrop_path")
                            existing_movie.popularity = details.get("popularity")
                            existing_movie.vote_average = details.get("vote_average")
                            existing_movie.vote_count = details.get("vote_count")
                            existing_movie.original_language = details.get("original_language") or lang
                            existing_movie.adult = details.get("adult", False)
                            existing_movie.status = details.get("status")

                            # Clear and re-associate genres & languages
                            existing_movie.genres.clear()
                            existing_movie.languages.clear()
                            movie_obj = existing_movie
                            self.stats["updated"] += 1
                        else:
                            movie_obj = Movie(
                                tmdb_id=tmdb_id,
                                title=details.get("title") or item.get("title", ""),
                                original_title=details.get("original_title") or item.get("original_title"),
                                overview=details.get("overview") or item.get("overview"),
                                release_date=rel_date,
                                runtime_minutes=details.get("runtime"),
                                poster_path=details.get("poster_path"),
                                backdrop_path=details.get("backdrop_path"),
                                popularity=details.get("popularity"),
                                vote_average=details.get("vote_average"),
                                vote_count=details.get("vote_count"),
                                original_language=details.get("original_language") or lang,
                                adult=details.get("adult", False),
                                status=details.get("status"),
                            )
                            movie_repo.create(movie_obj)
                            self.stats["inserted"] += 1

                        # Attach genres
                        for g_data in details.get("genres", []):
                            g_id = g_data["id"]
                            g_name = g_data["name"]
                            genre = genre_repo.get_by_tmdb_id(g_id)
                            if not genre:
                                genre = Genre(tmdb_id=g_id, name=g_name, slug=slugify(g_name))
                                db.add(genre)
                                db.flush()
                            if genre not in movie_obj.genres:
                                movie_obj.genres.append(genre)

                        # Attach primary language
                        orig_lang_code = details.get("original_language") or lang
                        language_rec = lang_repo.get_by_iso(orig_lang_code)
                        if not language_rec:
                            language_rec = Language(
                                iso_639_1=orig_lang_code,
                                english_name=orig_lang_code.upper(),
                                native_name=orig_lang_code.upper(),
                            )
                            db.add(language_rec)
                            db.flush()
                        if language_rec not in movie_obj.languages:
                            movie_obj.languages.append(language_rec)

                        TrailerService(db).upsert(movie_obj, details.get("videos", {}))

                    # Commit database transaction per page
                    db.commit()
                    print(f"  [Page {page}/{total_pages}] {lang.upper()} {year} batch committed.")
                    page += 1

                except Exception as page_err:
                    db.rollback()
                    self.stats["errors"] += 1
                    print(f"  [ERROR] Page {page} failed for {lang} {year}: {page_err}")
                    page += 1
        finally:
            db.close()
