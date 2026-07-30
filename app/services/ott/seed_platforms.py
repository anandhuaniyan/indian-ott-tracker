from app.database.session import SessionLocal
from app.models.ott_platform import OttPlatform


PLATFORMS = [
    {
        "name": "Netflix",
        "slug": "netflix",
        "website_url": "https://www.netflix.com",
    },
    {
        "name": "Amazon Prime Video",
        "slug": "amazon-prime-video",
        "website_url": "https://www.primevideo.com",
    },
    {
        "name": "JioHotstar",
        "slug": "jiohotstar",
        "website_url": "https://www.jiohotstar.com",
    },
    {
        "name": "Sony LIV",
        "slug": "sonyliv",
        "website_url": "https://www.sonyliv.com",
    },
    {
        "name": "ZEE5",
        "slug": "zee5",
        "website_url": "https://www.zee5.com",
    },
    {
        "name": "Sun NXT",
        "slug": "sun-nxt",
        "website_url": "https://www.sunnxt.com",
    },
    {
        "name": "Aha",
        "slug": "aha",
        "website_url": "https://www.aha.video",
    },
    {
        "name": "ManoramaMAX",
        "slug": "manoramamax",
        "website_url": "https://www.manoramamax.com",
    },
]


def seed_platforms():

    db = SessionLocal()

    try:

        for item in PLATFORMS:

            existing = (
                db.query(OttPlatform)
                .filter(
                    OttPlatform.slug == item["slug"]
                )
                .first()
            )

            if existing:
                continue

            platform = OttPlatform(
                name=item["name"],
                slug=item["slug"],
                website_url=item["website_url"],
                logo_url=None,
                is_active=True,
            )

            db.add(platform)

        db.commit()

        print("OTT platforms seeded successfully")

    finally:
        db.close()


if __name__ == "__main__":
    seed_platforms()