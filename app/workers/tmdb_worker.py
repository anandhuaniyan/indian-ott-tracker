from app.services.tmdb.sync_movies import sync_latest_movies


def run():
    print("Starting TMDb sync...")
    sync_latest_movies(max_pages=5)
    print("TMDb sync finished.")


if __name__ == "__main__":
    run()