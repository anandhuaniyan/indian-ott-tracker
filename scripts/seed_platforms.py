"""Seed Indian OTT platforms into the database."""

from dataclasses import dataclass

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.ott_platform import OttPlatform


@dataclass(frozen=True)
class PlatformSeed:
    """Seed data for a single OTT platform."""

    name: str
    website_url: str


INDIAN_OTT_PLATFORMS: tuple[PlatformSeed, ...] = (
    PlatformSeed("Netflix", "https://www.netflix.com/in/"),
    PlatformSeed("Amazon Prime Video", "https://www.primevideo.com/"),
    PlatformSeed("Disney+ Hotstar", "https://www.hotstar.com/in"),
    PlatformSeed("JioCinema", "https://www.jiocinema.com/"),
    PlatformSeed("Sony LIV", "https://www.sonyliv.com/"),
    PlatformSeed("Zee5", "https://www.zee5.com/"),
    PlatformSeed("MX Player", "https://www.mxplayer.in/"),
    PlatformSeed("Apple TV+", "https://tv.apple.com/in"),
    PlatformSeed("YouTube", "https://www.youtube.com"),
    PlatformSeed("Sun NXT", "https://www.sunnxt.com/"),
    PlatformSeed("Aha", "https://www.aha.video/"),
    PlatformSeed("Manorama MAX", "https://www.manoramamax.com/"),
)


def seed_platforms(session: Session) -> int:
    """Insert missing OTT platforms. Returns the number of newly created rows."""

    created_count = 0

    for platform in INDIAN_OTT_PLATFORMS:
        platform_slug = slugify(platform.name)
        existing = session.scalar(
            select(OttPlatform).where(OttPlatform.slug == platform_slug)
        )

        if existing is not None:
            continue

        session.add(
            OttPlatform(
                name=platform.name,
                slug=platform_slug,
                website_url=platform.website_url,
                is_active=True,
            )
        )
        created_count += 1

    session.commit()
    return created_count


def main() -> None:
    """Run the OTT platform seed script."""

    session = SessionLocal()

    try:
        created_count = seed_platforms(session)
        print(f"Seeded {created_count} OTT platform(s).")
    finally:
        session.close()


if __name__ == "__main__":
    main()
