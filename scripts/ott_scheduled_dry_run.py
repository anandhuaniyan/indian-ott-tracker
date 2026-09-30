"""Zero-credit dry run: preview scheduled OTT candidates without searching."""
import json
from datetime import date, datetime, timezone
from sqlalchemy import case, exists, func, select

from app.database.connection import SessionLocal
from app.models.movie import Movie
from app.models.operations import MovieRequest, OttEvidence


def candidates(db, limit):
    now = datetime.now(timezone.utc)
    requested = exists(
        select(MovieRequest.id).where(
            func.lower(MovieRequest.movie_name) == func.lower(Movie.title),
            MovieRequest.status.in_(["PENDING", "REVIEWING", "FOUND"]),
        )
    )
    return (
        db.query(OttEvidence)
        .join(Movie, Movie.id == OttEvidence.movie_id)
        .filter(
            Movie.ott_research_eligibility == "ELIGIBLE",
            OttEvidence.source_url.is_(None),
            OttEvidence.status.in_(
                ["UNKNOWN", "QUEUED", "POSSIBLE", "NOT_FOUND", "CONFLICTING", "FAILED"]
            ),
            OttEvidence.next_check <= now,
        )
        .order_by(
            case((requested, 0), else_=1),
            Movie.theatrical_release_date.desc(),
            Movie.popularity.desc(),
            OttEvidence.next_check,
        )
        .limit(limit)
        .all()
    )


def run():
    db = SessionLocal()
    try:
        out = {"as_of": datetime.now(timezone.utc).isoformat()}
        for label, limit in (("daily", 10), ("weekly", 25)):
            rows = candidates(db, limit)
            bucket = []
            for item in rows:
                movie = db.get(Movie, item.movie_id)
                bucket.append(
                    {
                        "movie_id": item.movie_id,
                        "title": movie.title if movie else None,
                        "status": item.status,
                        "next_check": item.next_check.isoformat() if item.next_check else None,
                        "theatrical_release_date": (
                            movie.theatrical_release_date.isoformat()
                            if movie and movie.theatrical_release_date else None
                        ),
                        "release_age_days": (
                            (date.today() - movie.theatrical_release_date).days
                            if movie and movie.theatrical_release_date else None
                        ),
                        "original_language": movie.original_language if movie else None,
                    }
                )
            out[label] = {"limit": limit, "candidates": len(bucket), "items": bucket}
        print(json.dumps(out, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    run()