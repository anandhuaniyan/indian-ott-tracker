from sqlalchemy.orm import Session

from app.repositories.movie_repository import MovieRepository


class MovieService:

    def __init__(self, db: Session):
        self.repository = MovieRepository(db)

    def count_movies(self):
        return self.repository.count()

    def get_movies(
        self,
        page: int = 1,
        page_size: int = 20,
    ):
        return self.repository.get_all(page, page_size)

    def get_movie(self, movie_id: int):
        return self.repository.get_by_id(movie_id)

    def search_movies(self, query: str):
        return self.repository.search(query)