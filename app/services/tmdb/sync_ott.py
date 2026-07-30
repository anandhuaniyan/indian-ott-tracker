from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.movie_ott import MovieOtt
from app.models.ott_platform import OttPlatform
from app.models.movie import Movie

from app.services.tmdb.ott_service import TMDbOttService


tmdb_service = TMDbOttService()


PLATFORM_MAPPING = {

    "Netflix": "netflix",

    "Amazon Prime Video": "amazon-prime-video",

    "JioHotstar": "jiohotstar",

    "Disney Plus": "jiohotstar",

    "Sony Liv": "sonyliv",

    "ZEE5": "zee5",

    "Sun Nxt": "sun-nxt",

    "Aha": "aha",

    "ManoramaMAX": "manoramamax",
}



def sync_movie_ott():

    db: Session = SessionLocal()

    inserted = 0
    updated = 0


    try:

        movies = db.query(Movie).all()


        platforms = {
            p.slug: p
            for p in db.query(OttPlatform).all()
        }


        for movie in movies:


            providers = tmdb_service.get_movie_watch_providers(
                movie.tmdb_id
            )


            flatrate = providers.get("flatrate", [])


            for provider in flatrate:


                provider_name = provider.get("provider_name")


                slug = PLATFORM_MAPPING.get(provider_name)


                if not slug:
                    continue


                platform = platforms.get(slug)


                if not platform:
                    continue



                existing = (
                    db.query(MovieOtt)
                    .filter(
                        MovieOtt.movie_id == movie.id,
                        MovieOtt.platform_id == platform.id,
                        MovieOtt.region == "IN",
                    )
                    .first()
                )


                if existing:

                    existing.last_checked_at = datetime.now(
                        timezone.utc
                    )

                    updated += 1


                else:

                    record = MovieOtt(

                        movie_id=movie.id,

                        platform_id=platform.id,

                        region="IN",

                        watch_url=None,

                        last_checked_at=datetime.now(
                            timezone.utc
                        )

                    )

                    db.add(record)

                    inserted += 1



        db.commit()


        print(
            f"Inserted: {inserted}, Updated: {updated}"
        )


    finally:

        db.close()



if __name__ == "__main__":

    sync_movie_ott()