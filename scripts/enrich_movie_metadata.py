"""Enrich existing movies from TMDB without creating or replacing movies."""

import argparse

from app.database.connection import SessionLocal
from app.models.movie import Movie
from app.services.movie_metadata_service import MovieMetadataService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--movie-id", type=int)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(Movie).order_by(Movie.id)
        movies = [query.filter(Movie.id == args.movie_id).one()] if args.movie_id else query.limit(args.limit).all()
        service = MovieMetadataService(db)
        for movie in movies:
            service.enrich_movie(movie)
            print(f"Enriched movie {movie.id} (TMDB {movie.tmdb_id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
