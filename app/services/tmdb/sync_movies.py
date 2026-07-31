from datetime import date
import time
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.genre import Genre
from app.models.language import Language
from app.models.movie import Movie
from app.repositories.genre_repository import GenreRepository
from app.repositories.language_repository import LanguageRepository
from app.repositories.movie_repository import MovieRepository
from app.services.tmdb.movie_service import TMDbMovieService

service = TMDbMovieService()

LANGUAGES = ["ml","ta","te","hi","kn"]
START_YEAR = 2011
END_YEAR = date.today().year

def sync_movies():
    db: Session = SessionLocal()
    movie_repo = MovieRepository(db)
    genre_repo = GenreRepository(db)
    language_repo = LanguageRepository(db)

    inserted = 0
    updated = 0

    try:
        for year in range(START_YEAR, END_YEAR + 1):
            for language in LANGUAGES:
                first = service.discover_movies_by_language_and_year(language, year, 1)
                total_pages = first.get("total_pages", 1)

                for page in range(1, total_pages + 1):
                    if page == 1:
                        data = first
                    else:
                        data = service.discover_movies_by_language_and_year(language, year, page)

                    print(f"Year={year} Language={language} Page={page}/{total_pages}")

                    for item in data.get("results", []):
                        details = service.get_movie_details(item["id"])
                        movie = movie_repo.get_by_tmdb_id(item["id"])

                        if movie:
                            movie.title = details.get("title")
                            movie.original_title = details.get("original_title")
                            movie.overview = details.get("overview")
                            movie.release_date = details.get("release_date") or None
                            movie.runtime_minutes = details.get("runtime")
                            movie.poster_path = details.get("poster_path")
                            movie.backdrop_path = details.get("backdrop_path")
                            movie.popularity = details.get("popularity")
                            movie.vote_average = details.get("vote_average")
                            movie.vote_count = details.get("vote_count")
                            movie.original_language = details.get("original_language")
                            movie.adult = details.get("adult", False)
                            movie.genres.clear()
                            movie.languages.clear()
                            updated += 1
                        else:
                            movie = Movie(
                                tmdb_id=details["id"],
                                title=details.get("title"),
                                original_title=details.get("original_title"),
                                overview=details.get("overview"),
                                release_date=details.get("release_date") or None,
                                runtime_minutes=details.get("runtime"),
                                poster_path=details.get("poster_path"),
                                backdrop_path=details.get("backdrop_path"),
                                popularity=details.get("popularity"),
                                vote_average=details.get("vote_average"),
                                vote_count=details.get("vote_count"),
                                original_language=details.get("original_language"),
                                adult=details.get("adult", False),
                            )
                            movie_repo.create(movie)
                            inserted += 1

                        for genre_data in details.get("genres", []):
                            genre = genre_repo.get_by_tmdb_id(genre_data["id"])
                            if not genre:
                                genre = Genre(
                                    tmdb_id=genre_data["id"],
                                    name=genre_data["name"],
                                    slug=genre_data["name"].lower().replace(" ","-"),
                                )
                                db.add(genre)
                                db.flush()
                            movie.genres.append(genre)

                        lang = details.get("original_language")
                        if lang:
                            language_obj = language_repo.get_by_iso(lang)
                            if not language_obj:
                                language_obj = Language(
                                    iso_639_1=lang,
                                    english_name=lang.upper(),
                                    native_name=lang.upper(),
                                )
                                db.add(language_obj)
                                db.flush()
                            movie.languages.append(language_obj)

                    db.commit()
                    time.sleep(0.25)

        print(f"Import completed. Inserted={inserted} Updated={updated}")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    sync_movies()
