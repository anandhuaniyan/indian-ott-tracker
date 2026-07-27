from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.movie import Movie
from app.repositories.movie_repository import MovieRepository
from app.services.tmdb.movie_service import TMDbMovieService


service = TMDbMovieService()


def sync_latest_movies(max_pages: int = 5):

    db: Session = SessionLocal()
    repo = MovieRepository(db)

    inserted = 0
    updated = 0

    try:

        for page in range(1, max_pages + 1):

            data = service.discover_indian_movies(page=page)

            print(f"Page {page}: {len(data['results'])} movies")

            for item in data["results"]:

                movie = repo.get_by_tmdb_id(item["id"])

                if movie:
                    repo.update_from_tmdb(movie, item)
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

                repo.create(movie)
                inserted += 1

            repo.save()

        print()
        print("========== Sync Complete ==========")
        print(f"Inserted : {inserted}")
        print(f"Updated  : {updated}")

    finally:
        db.close()


if __name__ == "__main__":
    sync_latest_movies()