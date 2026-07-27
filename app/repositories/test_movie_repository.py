from app.database.session import SessionLocal
from app.repositories.movie_repository import MovieRepository


db = SessionLocal()

repo = MovieRepository(db)

print("Movie count:", repo.count())

db.close()