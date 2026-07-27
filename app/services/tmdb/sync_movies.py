from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.movie import Movie
from app.services.tmdb.movie_service import TMDbMovieService

service = TMDbMovieService()


def sync_latest_movies(max_pages: int = 5):
    db: Session = SessionLocal()

    inserted = 0
    updated = 0
    skipped = 0

    try:
        for page in range(1, max_pages + 1):
            data = service.discover_indian_movies(page=page)

            print(f"Page {page}: {len(data['results'])} movies")

            if not data["results"]:
                break

            for item in data["results"]:

                movie = (
                    db.query(Movie)
                    .filter(Movie.tmdb_id == item["id"])
                    .first()
                )

                if movie:
                    movie.title = item["title"]
                    movie.original_title = item.get("original_title")
                    movie.overview = item.get("overview")
                    movie.release_date = item.get("release_date") or None
                    movie.poster_path = item.get("poster_path")
                    movie.backdrop_path = item.get("backdrop_path")
                    movie.popularity = item.get("popularity")
                    movie.vote_average = item.get("vote_average")
                    movie.vote_count = item.get("vote_count")
                    movie.original_language = item.get("original_language")
                    movie.adult = item.get("adult", False)

                    updated += 1
                    continue

                movie = Movie(
                    tmdb_id=item["id"],
                    title=item["title"],
                    original_title=item.get("original_title"),
                    overview=item.get("overview"),
                    release_date=item.get("release_date") or None,
                    poster_path=item.get("poster_path"),
                    backdrop_path=item.get("backdrop_path"),
                    popularity=item.get("popularity"),
                    vote_average=item.get("vote_average"),
                    vote_count=item.get("vote_count"),
                    original_language=item.get("original_language"),
                    adult=item.get("adult", False),
                )

                db.add(movie)
                inserted += 1

            db.commit()

        print()
        print("========== Sync Complete ==========")
        print(f"Inserted : {inserted}")
        print(f"Updated  : {updated}")
        print(f"Skipped  : {skipped}")

    finally:
        db.close()


if __name__ == "__main__":
    sync_latest_movies(max_pages=5)