from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.language import Language
from app.repositories.language_repository import LanguageRepository
from app.services.tmdb.client import TMDbClient


client = TMDbClient()


def sync_languages():

    db: Session = SessionLocal()
    repo = LanguageRepository(db)

    try:

        response = client.get("/configuration/languages")

        for item in response:

            iso = item.get("iso_639_1")

            if not iso:
                continue

            language = repo.get_by_iso(iso)

            if language:
                language.english_name = item.get("english_name")
                language.native_name = item.get("name")
                continue

            repo.create(
                Language(
                    iso_639_1=iso,
                    english_name=item.get("english_name"),
                    native_name=item.get("name"),
                )
            )

        repo.save()

        print("Languages synchronized successfully.")

    finally:
        db.close()


if __name__ == "__main__":
    sync_languages()