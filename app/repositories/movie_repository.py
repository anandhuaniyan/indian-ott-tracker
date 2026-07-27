from sqlalchemy.orm import Session

from app.models.movie import Movie


class MovieRepository:

    def __init__(self, db: Session):
        self.db = db

    def get_by_tmdb_id(self, tmdb_id: int):
        return (
            self.db.query(Movie)
            .filter(Movie.tmdb_id == tmdb_id)
            .first()
        )

    def create(self, movie: Movie):
        self.db.add(movie)

    def save(self):
        self.db.commit()

    def count(self):
        return self.db.query(Movie).count()

    def get_all(
   	 self,
   	 page: int = 1,
   	 page_size: int = 20,
    ):
    	return (
       		 self.db.query(Movie)
       		 .order_by(Movie.release_date.desc())
       		 .offset((page - 1) * page_size)
      		  .limit(page_size)
      		  .all()
    	)
    def get_by_id(self, movie_id: int):
        return (
            self.db.query(Movie)
            .filter(Movie.id == movie_id)
            .first()
        )

    def search(self, query: str):
        return (
            self.db.query(Movie)
            .filter(Movie.title.ilike(f"%{query}%"))
            .all()
        )

    def update_from_tmdb(self, movie: Movie, item: dict):
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