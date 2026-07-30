from sqlalchemy.orm import Session

from app.models.language import Language


class LanguageRepository:

    def __init__(self, db: Session):
        self.db = db

    def get_by_iso(self, iso: str):
        return (
            self.db.query(Language)
            .filter(Language.iso_639_1 == iso)
            .first()
        )

    def get_all(self):
        return (
            self.db.query(Language)
            .order_by(Language.english_name)
            .all()
        )

    def create(self, language: Language):
        self.db.add(language)

    def save(self):
        self.db.commit()