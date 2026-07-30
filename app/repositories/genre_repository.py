from sqlalchemy.orm import Session

from app.models.genre import Genre


class GenreRepository:

    def __init__(self, db: Session):
        self.db = db

    def get_by_tmdb_id(self, tmdb_id: int):
        return (
            self.db.query(Genre)
            .filter(Genre.tmdb_id == tmdb_id)
            .first()
        )

    def get_by_slug(self, slug: str):
        return (
            self.db.query(Genre)
            .filter(Genre.slug == slug)
            .first()
        )

    def get_all(self):
        return (
            self.db.query(Genre)
            .order_by(Genre.name)
            .all()
        )

    def create(self, genre: Genre):
        self.db.add(genre)

    def save(self):
        self.db.commit()
