from app.services.tmdb.movie_service import TMDbMovieService


service = TMDbMovieService()

movies = service.discover_indian_movies()

print(f"Found {len(movies['results'])} movies\n")

for movie in movies["results"][:10]:
    print(
        movie["title"],
        "|",
        movie.get("release_date"),
        "|",
        movie.get("original_language"),
    )