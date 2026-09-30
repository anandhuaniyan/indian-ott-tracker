"""Standalone pin generation demo -- no database required.

Demonstrates the OTTTracker pin image generator with sample movie data
and existing poster artwork from the media directory.

Run: python scripts/pinterest_demo.py
"""

import sys
import os
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pinterest.pin_generator import generate_pin, generate_branded_only_pin, TEMPLATES

DEMO_MOVIES = [
    {
        "id": 78,
        "title": "DC",
        "original_title": "DC",
        "original_language": "ta",
        "release_date": 2026,
        "genres": ["Action", "Thriller"],
        "ott_platform": "Prime Video",
        "ott_release_date": "7 August 2026",
        "vote_average": 7.8,
    },
    {
        "id": 80,
        "title": "Marco",
        "original_title": "Marco",
        "original_language": "ml",
        "release_date": 2025,
        "genres": ["Action", "Drama"],
        "ott_platform": "SonyLIV",
        "ott_release_date": "15 January 2026",
        "vote_average": 8.1,
    },
    {
        "id": 82,
        "title": "Thug Life",
        "original_title": "Thug Life",
        "original_language": "ta",
        "release_date": 2025,
        "genres": ["Action", "Crime"],
        "ott_platform": "Netflix",
        "ott_release_date": None,
        "vote_average": 6.5,
    },
    {
        "id": 8641,
        "title": "Kantara Chapter 1",
        "original_title": "Kantara Chapter 1",
        "original_language": "kn",
        "release_date": 2025,
        "genres": ["Action", "Drama", "Fantasy"],
        "ott_platform": None,
        "ott_release_date": None,
        "vote_average": 8.5,
    },
    {
        "id": 88,
        "title": "Identity",
        "original_title": "Identity",
        "original_language": "en",
        "release_date": 2025,
        "genres": ["Thriller", "Mystery"],
        "ott_platform": "Disney+ Hotstar",
        "ott_release_date": "20 December 2025",
        "vote_average": 7.2,
    },
]

TEMPLATE_ASSIGNMENT = ["NOW_STREAMING", "OTT_RELEASE", "MOVIE_DISCOVERY", "COMING_SOON", "HIGHLY_RATED"]


def find_poster(movie_id: int) -> str | None:
    """Find a local poster file for a movie."""
    poster_dir = Path("media/posters")
    if not poster_dir.exists():
        return None
    for f in poster_dir.iterdir():
        if f.is_file() and f.name.startswith(f"movie-{movie_id}-"):
            return str(f)
    return None


def main():
    print("=" * 60)
    print("OTTTRACKER — Pinterest Pin Generation Demo")
    print("=" * 60)
    print()

    output_dir = Path("media/pinterest")
    output_dir.mkdir(parents=True, exist_ok=True)

    generated = []

    for i, (movie, template_name) in enumerate(zip(DEMO_MOVIES, TEMPLATE_ASSIGNMENT), 1):
        print(f"\n{'─' * 50}")
        print(f"PIN {i} of {len(DEMO_MOVIES)}")
        print(f"{'─' * 50}")

        poster_path = find_poster(movie["id"])
        has_artwork = poster_path is not None

        try:
            if has_artwork:
                filepath, rel_path = generate_pin(
                    movie=movie,
                    template_name=template_name,
                    artwork_path=poster_path,
                    artwork_source="poster",
                    output_dir=str(output_dir),
                )
            else:
                filepath, rel_path = generate_branded_only_pin(
                    movie=movie,
                    template_name=template_name,
                    output_dir=str(output_dir),
                )

            site_url = "https://otttracker.in"
            dest_url = f"{site_url}/movies/{movie['id']}"

            lang_display = {
                "ml": "Malayalam", "ta": "Tamil", "te": "Telugu",
                "hi": "Hindi", "kn": "Kannada", "en": "English",
            }.get(movie["original_language"], movie["original_language"].upper())

            pin_title_templates = {
                "NOW_STREAMING": f"{movie['title']} ({movie['release_date']}) — Now Streaming on OTT",
                "OTT_RELEASE": f"{movie['title']} OTT Release — Streaming Details",
                "COMING_SOON": f"{movie['title']} ({movie['release_date']}) — Coming Soon to OTT",
                "HIGHLY_RATED": f"{movie['title']} ({movie['release_date']}) — Highly Rated {lang_display} Film",
                "MOVIE_DISCOVERY": f"{movie['title']} ({movie['release_date']}) — Where to Watch Online",
            }
            pin_title = pin_title_templates.get(template_name, pin_title_templates["MOVIE_DISCOVERY"])

            desc_parts = [f"{movie['title']} ({movie['release_date']})"]
            genres_str = " ".join(movie["genres"][:2])
            desc_parts.append(f"is a {lang_display} {genres_str} film.")
            if movie["ott_platform"]:
                if movie["ott_release_date"]:
                    desc_parts.append(f"Streaming on {movie['ott_platform']} from {movie['ott_release_date']}.")
                else:
                    desc_parts.append(f"Streaming on {movie['ott_platform']}.")
            desc_parts.append("Find where to watch on OTTTracker.")
            pin_desc = " ".join(desc_parts)

            print(f"  MOVIE:              {movie['title']} (ID: {movie['id']})")
            print(f"  LANGUAGE:           {lang_display} ({movie['original_language'].upper()})")
            print(f"  PLATFORM:           {movie['ott_platform'] or 'Unknown'}")
            print(f"  OTT DATE:           {movie['ott_release_date'] or 'Unknown'}")
            print(f"  TEMPLATE:           {template_name}")
            print(f"  ARTWORK SOURCE:     {'HAS_POSTER' if has_artwork else 'BRANDED_ONLY'}")
            print(f"  ARTWORK RIGHTS:     RIGHTS_UNKNOWN")
            print(f"  DESTINATION URL:    {dest_url}")
            print(f"  PIN TITLE:          {pin_title}")
            print(f"  PIN DESCRIPTION:    {pin_desc}")
            print(f"  GENERATED IMAGE:    {rel_path}")

            generated.append({
                "movie": movie["title"],
                "movie_id": movie["id"],
                "template": template_name,
                "image": rel_path,
                "status": "SUCCESS",
            })

        except Exception as exc:
            print(f"  ERROR: {exc}")
            generated.append({
                "movie": movie["title"],
                "movie_id": movie["id"],
                "template": template_name,
                "image": None,
                "status": f"FAILED: {exc}",
            })

    print(f"\n{'=' * 60}")
    print("GENERATION SUMMARY")
    print(f"{'=' * 60}")
    for g in generated:
        status_icon = "OK" if g["status"] == "SUCCESS" else "FAIL"
        print(f"  [{status_icon}] {g['movie']} (ID: {g['movie_id']}) — {g['template']}")
        if g["image"]:
            print(f"       Image: {g['image']}")
    print()
    print(f"Images saved to: {output_dir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
