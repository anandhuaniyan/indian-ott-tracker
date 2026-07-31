"""Repository for managing OTT availability database operations."""

from datetime import datetime, date, timedelta, timezone

from sqlalchemy import or_, and_, func, select
from sqlalchemy.orm import Session

from app.models.movie import Movie
from app.models.ott_availability import OttAvailability


class OttAvailabilityRepository:
    """Handles CRUD operations and scheduling query logic for OTT Availability."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_movie_id(self, movie_id: int) -> list[OttAvailability]:
        """Fetch all OTT availability records for a specific movie."""
        return (
            self.db.query(OttAvailability)
            .filter(OttAvailability.movie_id == movie_id)
            .order_by(OttAvailability.provider)
            .all()
        )

    def upsert_provider(
        self,
        movie_id: int,
        provider: str,
        country: str = "IN",
        watch_type: str = "subscription",
        provider_logo: str | None = None,
        ott_release_date: date | None = None,
        status: str = "available",
        source_type: str = "tmdb",
        source_url: str | None = None,
        confidence: float = 100.0,
        last_checked: datetime | None = None,
    ) -> OttAvailability:
        """Insert or update an OTT availability record."""
        now = datetime.now(timezone.utc)
        check_time = last_checked or now

        record = (
            self.db.query(OttAvailability)
            .filter(
                OttAvailability.movie_id == movie_id,
                OttAvailability.provider == provider,
                OttAvailability.country == country,
                OttAvailability.watch_type == watch_type,
            )
            .first()
        )

        if record:
            record.provider_logo = provider_logo or record.provider_logo
            if ott_release_date:
                record.ott_release_date = ott_release_date
            record.status = status
            record.source_type = source_type
            if source_url:
                record.source_url = source_url
            record.confidence = confidence
            record.last_checked = check_time
        else:
            record = OttAvailability(
                movie_id=movie_id,
                provider=provider,
                provider_logo=provider_logo,
                country=country,
                watch_type=watch_type,
                ott_release_date=ott_release_date,
                status=status,
                source_type=source_type,
                source_url=source_url,
                confidence=confidence,
                last_checked=check_time,
            )
            self.db.add(record)

        return record

    def save(self):
        """Commit current transaction."""
        self.db.commit()

    def get_movies_due_for_sync(self, limit: int = 50) -> list[Movie]:
        """Find movies due for OTT sync based on search frequency rules:
        - Released within 30 days -> check daily (last_checked <= 1 day ago or NULL)
        - Released within 6 months -> check every 3 days (last_checked <= 3 days ago or NULL)
        - Older movies -> check monthly (last_checked <= 30 days ago or NULL)
        """
        now = datetime.now(timezone.utc)
        today = date.today()

        d30 = today - timedelta(days=30)
        d180 = today - timedelta(days=180)

        t1_day = now - timedelta(days=1)
        t3_days = now - timedelta(days=3)
        t30_days = now - timedelta(days=30)

        # Subquery to get max last_checked per movie
        subq = (
            self.db.query(
                OttAvailability.movie_id,
                func.max(OttAvailability.last_checked).label("last_checked"),
            )
            .group_by(OttAvailability.movie_id)
            .subquery()
        )

        query = (
            self.db.query(Movie)
            .outerjoin(subq, Movie.id == subq.c.movie_id)
            .filter(
                or_(
                    # Tier 1: Recent release (<=30 days) and unchecked in >1 day
                    and_(
                        Movie.release_date >= d30,
                        or_(subq.c.last_checked == None, subq.c.last_checked <= t1_day),
                    ),
                    # Tier 2: Mid release (<=180 days) and unchecked in >3 days
                    and_(
                        Movie.release_date < d30,
                        Movie.release_date >= d180,
                        or_(subq.c.last_checked == None, subq.c.last_checked <= t3_days),
                    ),
                    # Tier 3: Older release (>180 days) and unchecked in >30 days
                    and_(
                        Movie.release_date < d180,
                        or_(subq.c.last_checked == None, subq.c.last_checked <= t30_days),
                    ),
                    # Tier 4: Movies with no release date or never checked
                    subq.c.last_checked == None,
                )
            )
            .order_by(Movie.release_date.desc().nullslast())
            .limit(limit)
        )

        return query.all()
