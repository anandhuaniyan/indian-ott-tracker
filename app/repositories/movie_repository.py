from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models.movie import Movie
from app.models.genre import Genre
from app.models.language import Language


class MovieRepository:

    def __init__(self, db: Session):
        self.db = db


    def get_by_tmdb_id(
        self,
        tmdb_id: int
    ):

        return (
            self.db.query(Movie)
            .filter(
                Movie.tmdb_id == tmdb_id
            )
            .first()
        )


    def create(
        self,
        movie: Movie
    ):

        self.db.add(movie)


    def save(self):

        self.db.commit()


    def count(
        self,
        language=None,
        genre=None,
        year=None,
    ):

        query = self.db.query(Movie)


        if language:

            query = (
                query
                .join(Movie.languages)
                .filter(
                    Language.iso_639_1 == language
                )
            )


        if genre:

            query = (
                query
                .join(Movie.genres)
                .filter(
                    Genre.slug == genre
                )
            )


        if year:

            query = query.filter(
                Movie.release_date.between(
                    f"{year}-01-01",
                    f"{year}-12-31"
                )
            )


        return query.count()



    def get_all(
        self,
        page: int = 1,
        page_size: int = 20,
        language=None,
        genre=None,
        year=None,
        sort="latest",
    ):

        query = self.db.query(Movie)



        if language:

            query = (
                query
                .join(Movie.languages)
                .filter(
                    Language.iso_639_1 == language
                )
            )


        if genre:

            query = (
                query
                .join(Movie.genres)
                .filter(
                    Genre.slug == genre
                )
            )


        if year:

            query = query.filter(
                Movie.release_date.between(
                    f"{year}-01-01",
                    f"{year}-12-31"
                )
            )



        if sort == "rating":

            query = query.order_by(
                desc(Movie.vote_average)
            )


        elif sort == "popular":

            query = query.order_by(
                desc(Movie.popularity)
            )


        else:

            query = query.order_by(
                desc(Movie.release_date)
            )



        return (
            query
            .offset(
                (page - 1) * page_size
            )
            .limit(
                page_size
            )
            .all()
        )



    def get_by_id(
        self,
        movie_id: int
    ):

        return (
            self.db.query(Movie)
            .filter(
                Movie.id == movie_id
            )
            .first()
        )



    def search(
        self,
        query: str
    ):

        return (
            self.db.query(Movie)
            .filter(
                Movie.title.ilike(
                    f"%{query}%"
                )
            )
            .all()
        )



    def update_from_tmdb(
        self,
        movie: Movie,
        item: dict
    ):

        movie.title = item["title"]
        movie.original_title = item.get(
            "original_title"
        )
        movie.overview = item.get(
            "overview"
        )
        movie.release_date = item.get(
            "release_date"
        ) or None

        movie.poster_path = item.get(
            "poster_path"
        )

        movie.backdrop_path = item.get(
            "backdrop_path"
        )

        movie.popularity = item.get(
            "popularity"
        )

        movie.vote_average = item.get(
            "vote_average"
        )

        movie.vote_count = item.get(
            "vote_count"
        )

        movie.original_language = item.get(
            "original_language"
        )

        movie.adult = item.get(
            "adult",
            False
        )