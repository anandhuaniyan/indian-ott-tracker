from sqlalchemy.orm import Session

from app.repositories.movie_repository import MovieRepository


class MovieService:

    def __init__(self, db: Session):
        self.repository = MovieRepository(db)


    def count_movies(
        self,
        language=None,
        genre=None,
        year=None,
    ):
        return self.repository.count(
            language,
            genre,
            year,
        )


    def get_movies(
        self,
        page: int = 1,
        page_size: int = 20,
        language: str | None = None,
        genre: str | None = None,
        year: int | None = None,
        sort: str = "latest",
    ):

        return self.repository.get_all(
            page=page,
            page_size=page_size,
            language=language,
            genre=genre,
            year=year,
            sort=sort,
        )


    def get_movie(
        self,
        movie_id: int,
    ):

        return self.repository.get_by_id(
            movie_id
        )


    def search_movies(
        self,
        query: str,
    ):

        return self.repository.search(
            query
        )