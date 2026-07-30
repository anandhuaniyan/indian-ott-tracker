from slugify import slugify
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.genre import Genre
from app.repositories.genre_repository import GenreRepository
from app.services.tmdb.client import TMDbClient


client = TMDbClient()


def sync_genres():

    db: Session = SessionLocal()
    repo = GenreRepository(db)

    try:

        response = client.get("/genre/movie/list")

        for item in response.get("genres", []):

            genre = repo.get_by_tmdb_id(item["id"])

            if genre:
                genre.name = item["name"]
                genre.slug = slugify(item["name"])
                continue

            repo.create(
                Genre(
                    tmdb_id=item["id"],
                    name=item["name"],
                    slug=slugify(item["name"]),
                )
            )

        repo.save()

        print("Genres synchronized successfully.")

    finally:
        db.close()


if __name__ == "__main__":
    sync_genres()