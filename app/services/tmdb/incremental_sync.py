from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.movie import Movie
from app.services.tmdb.movie_service import TMDbMovieService
from app.services.trailers import TrailerService


class IncrementalTMDbSync:

    def __init__(self):
        self.client = TMDbMovieService()

    def run(self, max_pages: int = 500):

        db: Session = SessionLocal()

        inserted = 0
        updated = 0

        try:

            for page in range(1, max_pages + 1):

                data = self.client.discover_indian_movies(page=page)

                movies = data.get("results", [])

                if not movies:
                    print("No more pages.")
                    break

                print(f"Scanning page {page}...")

                page_has_new_movie = False

                for item in movies:

                    details = self.client.get_movie_details(item["id"])

                    movie = (
                        db.query(Movie)
                        .filter(Movie.tmdb_id == item["id"])
                        .first()
                    )

                    if movie:
                        movie.title = details["title"]
                        movie.original_title = details.get("original_title")
                        movie.overview = details.get("overview")
                        movie.release_date = details.get("release_date") or None
                        movie.poster_path = details.get("poster_path")
                        movie.backdrop_path = details.get("backdrop_path")
                        movie.popularity = details.get("popularity")
                        movie.vote_average = details.get("vote_average")
                        movie.vote_count = details.get("vote_count")
                        movie.original_language = details.get("original_language")
                        movie.adult = details.get("adult", False)

                        TrailerService(db).upsert(movie, details.get("videos", {}))

                        updated += 1
                        continue

                    page_has_new_movie = True

                    movie = Movie(
                        tmdb_id=item["id"],
                        title=details["title"],
                        original_title=details.get("original_title"),
                        overview=details.get("overview"),
                        release_date=details.get("release_date") or None,
                        poster_path=details.get("poster_path"),
                        backdrop_path=details.get("backdrop_path"),
                        popularity=details.get("popularity"),
                        vote_average=details.get("vote_average"),
                        vote_count=details.get("vote_count"),
                        original_language=details.get("original_language"),
                        adult=details.get("adult", False),
                    )
                    db.add(movie)
                    db.flush()
                    TrailerService(db).upsert(movie, details.get("videos", {}))

                    inserted += 1

                db.commit()

                if not page_has_new_movie:
                    print()
                    print("Reached previously synced data.")
                    break

        finally:
            db.close()

        print()
        print("========== Incremental Sync ==========")
        print(f"Inserted : {inserted}")
        print(f"Updated  : {updated}")
