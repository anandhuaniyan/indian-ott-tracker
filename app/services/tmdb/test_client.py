from app.services.tmdb.client import TMDbClient


client = TMDbClient()

movie = client.get("/movie/550")

print(movie["title"])
print(movie["release_date"])
print(movie["original_language"])