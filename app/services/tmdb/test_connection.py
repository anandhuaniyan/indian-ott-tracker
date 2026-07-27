import httpx

from app.config.settings import settings


def test_tmdb_connection():
    url = "https://api.themoviedb.org/3/configuration"

    response = httpx.get(
        url,
        params={"api_key": settings.TMDB_API_KEY},
        timeout=30,
    )

    print("Status:", response.status_code)

    if response.status_code == 200:
        print("✅ Connected to TMDb successfully")
    else:
        print(response.text)


if __name__ == "__main__":
    test_tmdb_connection()