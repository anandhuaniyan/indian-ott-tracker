"""Generate 5 preview Pinterest pins from the OTTTracker database.

Run: python -m scripts.generate_pinterest_previews
"""

import sys
import os

# Ensure app is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.connection import SessionLocal
from app.models.movie import Movie
from app.models.movie_metadata import MovieImage
from app.models.ott_availability import OttAvailability
from app.services.pinterest.pin_generator import generate_pin, generate_branded_only_pin
from app.services.languages import language_name


def find_preview_movies(db: Session, limit: int = 5) -> list[Movie]:
    """Find 5 diverse movies with poster artwork for preview generation."""
    movies = []

    # Strategy: find movies with posters across different languages
    languages_to_find = ["ml", "ta", "te", "hi", "en"]

    for lang in languages_to_find:
        if len(movies) >= limit:
            break
        movie = (
            db.query(Movie)
            .filter(
                Movie.original_language == lang,
                Movie.release_date.isnot(None),
            )
            .join(MovieImage, Movie.images)
            .filter(
                MovieImage.image_type == "poster",
                MovieImage.local_path.isnot(None),
            )
            .order_by(Movie.popularity.desc().nullslast())
            .first()
        )
        if movie and movie.id not in [m.id for m in movies]:
            movies.append(movie)

    # If we don't have enough, fill with popular movies
    if len(movies) < limit:
        existing_ids = [m.id for m in movies]
        more = (
            db.query(Movie)
            .filter(
                Movie.id.notin_(existing_ids),
                Movie.release_date.isnot(None),
            )
            .join(MovieImage, Movie.images)
            .filter(
                MovieImage.image_type == "poster",
                MovieImage.local_path.isnot(None),
            )
            .order_by(Movie.popularity.desc().nullslast())
            .limit(limit - len(movies))
            .all()
        )
        movies.extend(more)

    return movies[:limit]


def get_poster_path(movie: Movie, db: Session) -> str | None:
    """Get the local path to the movie's primary poster."""
    image = (
        db.query(MovieImage)
        .filter(
            MovieImage.movie_id == movie.id,
            MovieImage.image_type == "poster",
            MovieImage.local_path.isnot(None),
            MovieImage.is_primary == True,
        )
        .first()
    )
    if not image:
        image = (
            db.query(MovieImage)
            .filter(
                MovieImage.movie_id == movie.id,
                MovieImage.image_type == "poster",
                MovieImage.local_path.isnot(None),
            )
            .order_by(MovieImage.is_primary.desc(), MovieImage.width.desc())
            .first()
        )
    if image and image.local_path:
        full_path = Path(settings.MEDIA_ROOT).parent / image.local_path.lstrip("/")
        if full_path.exists():
            return str(full_path)
    return None


def get_ott_info(movie: Movie, db: Session) -> tuple[str | None, str | None]:
    """Get OTT platform and release date."""
    avail = (
        db.query(OttAvailability)
        .filter(
            OttAvailability.movie_id == movie.id,
            OttAvailability.status.in_(["available", "confirmed"]),
        )
        .order_by(OttAvailability.ott_release_date.desc().nullslast())
        .first()
    )
    if avail:
        return avail.provider, avail.ott_release_date.isoformat() if avail.ott_release_date else None
    return None, None


def determine_pin_type(movie: Movie) -> str:
    """Determine pin type based on movie status."""
    today = date.today()
    has_ott = any(
        a.status in ("available", "confirmed")
        for a in (movie.ott_availabilities or [])
    )
    if has_ott:
        ott_dates = [
            a.ott_release_date
            for a in (movie.ott_availabilities or [])
            if a.ott_release_date
        ]
        if ott_dates:
            latest = max(ott_dates)
            if latest <= today:
                return "NOW_STREAMING"
            elif (latest - today).days <= 14:
                return "THIS_WEEK_ON_OTT"
        return "OTT_RELEASE"

    if movie.release_status_code and movie.release_status_code.upper() in ("UPCOMING", "ANNOUNCED"):
        return "COMING_SOON"

    if movie.vote_average and movie.vote_average >= 7.5:
        return "HIGHLY_RATED"

    return "MOVIE_DISCOVERY"


def main():
    print("=" * 60)
    print("OTTTRACKER — Pinterest Pin Preview Generator")
    print("=" * 60)
    print()

    db = SessionLocal()
    try:
        movies = find_preview_movies(db, limit=5)
        if not movies:
            print("ERROR: No movies found with poster artwork.")
            return

        output_dir = str(Path(settings.MEDIA_ROOT) / "pinterest")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        for i, movie in enumerate(movies, 1):
            print(f"\n{'─' * 50}")
            print(f"PREVIEW {i} of {len(movies)}")
            print(f"{'─' * 50}")

            pin_type = determine_pin_type(movie)
            poster_path = get_poster_path(movie, db)
            ott_platform, ott_date = get_ott_info(movie, db)
            lang = movie.original_language or "unknown"
            year = movie.release_date.year if movie.release_date else None
            genres = [g.name for g in (movie.genres or [])[:3]]
            site_url = settings.SITE_URL.rstrip("/")

            movie_dict = {
                "id": movie.id,
                "title": movie.title,
                "original_title": movie.original_title,
                "original_language": lang,
                "release_date": year,
                "genres": genres,
                "ott_platform": ott_platform,
                "ott_release_date": ott_date,
                "vote_average": movie.vote_average,
            }

            try:
                if poster_path:
                    filepath, rel_path = generate_pin(
                        movie=movie_dict,
                        template_name=pin_type,
                        artwork_path=poster_path,
                        artwork_source="poster",
                        output_dir=output_dir,
                    )
                    artwork_status = "HAS_POSTER"
                else:
                    filepath, rel_path = generate_branded_only_pin(
                        movie=movie_dict,
                        template_name=pin_type,
                        output_dir=output_dir,
                    )
                    artwork_status = "BRANDED_ONLY"

                title = movie.title
                if year:
                    if pin_type == "NOW_STREAMING":
                        pin_title = f"{title} ({year}) — Now Streaming on OTT"
                    elif pin_type == "OTT_RELEASE":
                        pin_title = f"{title} OTT Release — Streaming Details"
                    elif pin_type == "COMING_SOON":
                        pin_title = f"{title} ({year}) — Coming Soon to OTT"
                    elif pin_type == "HIGHLY_RATED":
                        pin_title = f"{title} ({year}) — Highly Rated {lang.upper()} Film"
                    else:
                        pin_title = f"{title} ({year}) — Where to Watch Online"
                else:
                    pin_title = f"{title} — Where to Watch Online"

                pin_desc_parts = [f"{title}"]
                if year:
                    pin_desc_parts[0] += f" ({year})"
                if lang:
                    lang_display = {"ml": "Malayalam", "ta": "Tamil", "te": "Telugu", "hi": "Hindi", "kn": "Kannada", "en": "English"}.get(lang, lang.upper())
                    if genres:
                        pin_desc_parts.append(f"is a {lang_display} {' '.join(genres[:2])} film.")
                    else:
                        pin_desc_parts.append(f"is a {lang_display} film.")
                if ott_platform:
                    if ott_date:
                        pin_desc_parts.append(f"Streaming on {ott_platform} from {ott_date}.")
                    else:
                        pin_desc_parts.append(f"Streaming on {ott_platform}.")
                pin_desc_parts.append("Find where to watch on OTTTracker.")
                pin_desc = " ".join(pin_desc_parts)

                print(f"  MOVIE:              {movie.title} (ID: {movie.id})")
                print(f"  LOCAL ID:           {movie.id}")
                print(f"  LANGUAGE:           {lang.upper()}")
                print(f"  PLATFORM:           {ott_platform or 'Unknown'}")
                print(f"  OTT DATE:           {ott_date or 'Unknown'}")
                print(f"  TEMPLATE:           {pin_type}")
                print(f"  ARTWORK SOURCE:     {artwork_status}")
                print(f"  ARTWORK RIGHTS:     RIGHTS_UNKNOWN")
                print(f"  DESTINATION URL:    {site_url}/movies/{movie.id}")
                print(f"  PIN TITLE:          {pin_title}")
                print(f"  PIN DESCRIPTION:    {pin_desc}")
                print(f"  GENERATED IMAGE:    {rel_path}")

            except Exception as exc:
                print(f"  ERROR: {exc}")

        print(f"\n{'=' * 60}")
        print("All 5 preview pins generated.")
        print(f"Images saved to: {output_dir}")
        print(f"View at: {settings.SITE_URL.rstrip('/')}/media/pinterest/")
        print(f"{'=' * 60}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
