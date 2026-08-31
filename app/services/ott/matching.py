"""Conservative Indian movie identity matching for external OTT discoveries."""

from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models.movie import Movie
from app.models.movie_metadata import AlternativeTitle, ExternalId, MovieCredit, Person


def normalize_title(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


@dataclass(slots=True)
class MovieMatch:
    movie: Movie | None
    confidence: float
    reason: str
    status: str


class MovieMatchService:
    AUTO_MATCH_THRESHOLD = 85.0
    REVIEW_THRESHOLD = 70.0

    def __init__(self, db: Session):
        self.db = db

    def match(self, candidate) -> MovieMatch:
        tmdb_id = getattr(candidate, "tmdb_id", None)
        if tmdb_id:
            movie = self.db.query(Movie).filter_by(tmdb_id=int(tmdb_id)).first()
            if movie:
                return MovieMatch(movie, 100, "Exact TMDB ID", "MATCHED")
        imdb_id = (getattr(candidate, "imdb_id", None) or "").strip()
        if imdb_id:
            movie = (
                self.db.query(Movie)
                .join(ExternalId, ExternalId.movie_id == Movie.id)
                .filter(ExternalId.provider.ilike("imdb"), ExternalId.external_id == imdb_id)
                .first()
            )
            if movie:
                return MovieMatch(movie, 100, "Exact IMDb ID", "MATCHED")

        title_values = {
            normalize_title(getattr(candidate, "title", None)),
            normalize_title(getattr(candidate, "original_title", None)),
        } - {""}
        if not title_values:
            return MovieMatch(None, 0, "No usable title or provider ID", "REJECTED")
        token = max(title_values, key=len).split()[0]
        rows = (
            self.db.query(Movie)
            .options(selectinload(Movie.alternative_titles), selectinload(Movie.credits).selectinload(MovieCredit.person))
            .outerjoin(AlternativeTitle, AlternativeTitle.movie_id == Movie.id)
            .filter(or_(Movie.title.ilike(f"%{token}%"), Movie.original_title.ilike(f"%{token}%"), AlternativeTitle.title.ilike(f"%{token}%")))
            .distinct()
            .limit(100)
            .all()
        )
        scored: list[tuple[float, Movie, str]] = []
        for movie in rows:
            movie_titles = {normalize_title(movie.title), normalize_title(movie.original_title)}
            movie_titles.update(normalize_title(item.title) for item in movie.alternative_titles)
            movie_titles.discard("")
            exact_title = bool(title_values & movie_titles)
            if not exact_title:
                continue
            score, reasons = 45.0, ["exact normalized/alternate title"]
            expected_year = getattr(candidate, "year", None)
            actual_date = movie.theatrical_release_date or movie.release_date
            actual_year = actual_date.year if actual_date else None
            if expected_year and actual_year:
                difference = abs(int(expected_year) - actual_year)
                if difference > 1:
                    continue
                score += 25 if difference == 0 else 15
                reasons.append("year")
            language = (getattr(candidate, "language", None) or "").lower()
            if language and movie.original_language:
                if language != movie.original_language.lower():
                    continue
                score += 15
                reasons.append("language")
            runtime = getattr(candidate, "runtime_minutes", None)
            if runtime and movie.runtime_minutes and abs(runtime - movie.runtime_minutes) <= 10:
                score += 5
                reasons.append("runtime")
            names = {credit.person.name.casefold() for credit in movie.credits if credit.person}
            directors = {name.casefold() for name in getattr(candidate, "directors", ())}
            cast = {name.casefold() for name in getattr(candidate, "cast", ())}
            if directors and directors & names:
                score += 7
                reasons.append("director")
            if cast and cast & names:
                score += 3
                reasons.append("cast")
            scored.append((min(score, 99), movie, ", ".join(reasons)))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return MovieMatch(None, 0, "No identity-safe local candidate", "REJECTED")
        best = scored[0]
        if len(scored) > 1 and scored[1][0] == best[0]:
            return MovieMatch(None, best[0], "Ambiguous same-title candidates", "NEEDS_REVIEW")
        status = "MATCHED" if best[0] >= self.AUTO_MATCH_THRESHOLD else "NEEDS_REVIEW"
        return MovieMatch(best[1] if status == "MATCHED" else None, best[0], best[2], status)
