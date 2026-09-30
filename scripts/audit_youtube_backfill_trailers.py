"""Audit the most recent trailer-backfill cohort without using YouTube search.list.

The legacy schema does not record which upstream supplied a YouTube-hosted row.
Provenance is therefore reconstructed from the trailer-backfill record and a
trailer created within 30 seconds of that record's most recent attempt.  The
operation's ``trailers_added`` counter is per movie, so the cohort is limited to
that many distinct movies, newest first.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
import re

from sqlalchemy import and_, func

from app.config.settings import settings
from app.database.connection import SessionLocal
from app.models.movie import Movie
from app.models.movie_metadata import MovieTrailer
from app.models.operations import BackfillRecord, OperationState
from app.services.youtube_trailer import YouTubeTrailerService


INVALID_PATTERNS = (
    ("REACTION_OR_REVIEW", re.compile(r"\b(reaction|review|explained|ending explained)\b", re.I)),
    ("FAN_MADE", re.compile(r"\b(fan[ -]?made|fan trailer|concept trailer|ai trailer|fake trailer|parody)\b", re.I)),
    ("SONG_OR_MUSIC_VIDEO", re.compile(r"\b(song|lyric(?:al)?|music video|jukebox|audio)\b", re.I)),
    ("MOVIE_SCENE_OR_CLIP", re.compile(r"\b(scene|clip|fight scene|comedy scene)\b", re.I)),
    ("INTERVIEW_OR_PROMO", re.compile(r"\b(interview|making|behind the scenes|\bbts\b|featurette|promo)\b", re.I)),
    ("SHORTS_VIDEO", re.compile(r"(?:#shorts?\b|\bshorts\b)", re.I)),
)
OFFICIAL_CHANNEL = re.compile(
    r"\b(official|studio|pictures|films?|movies|entertainment|productions?|distribution|"
    r"netflix|prime video|disney|sony|warner|universal|paramount|lionsgate|t-series|"
    r"saregama|sun tv|sony music|muzik247|think music|zee|manorama|max|jiohotstar)\b",
    re.I,
)
WORD = re.compile(r"[^\W_]+", re.UNICODE)
STOP = {"the", "a", "an", "of", "and", "or", "in", "on", "at", "for", "from", "movie", "film"}


def words(value: str | None) -> set[str]:
    return {part for part in WORD.findall((value or "").lower()) if len(part) > 2 and part not in STOP}


def compact(value: str | None) -> str:
    return "".join(WORD.findall((value or "").lower()))


def duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)", value)
    if not match:
        return None
    hours, minutes, seconds = (int(item or 0) for item in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def classify(movie: Movie, trailer: MovieTrailer, video: dict | None) -> tuple[str, str, str]:
    if not video:
        return "UNAVAILABLE_OR_DELETED", "HIGH", "videos.list returned no item"
    status = video.get("status") or {}
    if status.get("embeddable") is False:
        return "NOT_EMBEDDABLE", "HIGH", "YouTube reports embeddable=false"
    snippet = video.get("snippet") or {}
    title = (snippet.get("title") or trailer.name or "").strip()
    description = snippet.get("description") or ""
    channel = snippet.get("channelTitle") or ""
    combined = f"{title} {description[:1000]}"
    movie_words = words(movie.title) | words(movie.original_title)
    video_words = words(title)
    overlap = len(movie_words & video_words) / max(1, len(movie_words))
    known_titles = [value for value in (movie.title, movie.original_title) if value]
    substring_match = any(compact(value) and compact(value) in compact(title) for value in known_titles)
    fuzzy_match = any(SequenceMatcher(None, compact(value), compact(title)[: max(len(compact(value)), 1)]).ratio() >= 0.78 for value in known_titles)
    title_match = overlap >= 0.6 or substring_match or fuzzy_match
    for category, pattern in INVALID_PATTERNS:
        if pattern.search(title):
            confidence = "HIGH" if title_match or category in {"REACTION_OR_REVIEW", "FAN_MADE"} else "MEDIUM"
            return category, confidence, f"video title contains disallowed content marker: {pattern.search(title).group(0)}"
    movie_year = movie.release_date.year if movie.release_date else None
    mentioned_years = {int(item) for item in re.findall(r"\b(?:19|20)\d{2}\b", combined)}
    plausible_years = {movie_year - 1, movie_year, movie_year + 1} if movie_year else set()
    if movie_year and mentioned_years and not (mentioned_years & plausible_years) and title_match:
        return "WRONG_YEAR_OR_REMAKE", "MEDIUM", f"movie year {movie_year}; video mentions {sorted(mentioned_years)}"
    if not title_match:
        confidence = "HIGH" if movie.original_language in {None, "en"} and movie_words and not (movie_words & video_words) else "MEDIUM"
        return "WRONG_MOVIE", confidence, f"weak title overlap ({overlap:.2f})"
    seconds = duration_seconds((video.get("contentDetails") or {}).get("duration"))
    official_signal = bool(OFFICIAL_CHANNEL.search(channel))
    if re.search(r"\bteaser\b", title, re.I):
        if seconds is not None and seconds < 10:
            return "NEEDS_REVIEW", "MEDIUM", f"very short teaser ({seconds}s)"
        return "VALID_OFFICIAL_TEASER", "HIGH" if official_signal else "MEDIUM", "strong title match; teaser content"
    if not re.search(r"\btrailer\b", title, re.I):
        return "NEEDS_REVIEW", "MEDIUM", "strong movie match but title does not identify a trailer"
    if official_signal and re.search(r"\bofficial\b", title, re.I):
        return "VALID_OFFICIAL_TRAILER", "HIGH", "strong title match and official channel/title signals"
    if official_signal:
        return "VALID_DISTRIBUTOR_UPLOAD", "HIGH", "strong title match from studio/distributor-style channel"
    if overlap >= 0.75:
        return "LIKELY_VALID", "MEDIUM", "strong title match; independent or unrecognized channel"
    return "NEEDS_REVIEW", "LOW", f"partial title match ({overlap:.2f}) and weak channel evidence"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/youtube-backfill-trailer-audit.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cohort-size", type=int)
    args = parser.parse_args()
    db = SessionLocal()
    operation = db.query(OperationState).filter_by(name="tmdb.trailer_backfill").one()
    cohort_size = args.cohort_size or int((operation.details or {}).get("trailers_added") or 0)
    if cohort_size <= 0:
        raise RuntimeError("Trailer cohort size is unavailable; pass --cohort-size from the preserved operation report")
    rows = (
        db.query(MovieTrailer, Movie, BackfillRecord)
        .join(Movie, Movie.id == MovieTrailer.movie_id)
        .join(
            BackfillRecord,
            and_(
                BackfillRecord.operation == "tmdb.trailer_backfill",
                BackfillRecord.entity_type == "movie",
                BackfillRecord.entity_id == Movie.id,
            ),
        )
        .filter(
            MovieTrailer.is_primary.is_(True),
            func.abs(func.extract("epoch", MovieTrailer.created_at - BackfillRecord.last_attempt_at)) <= 30,
        )
        .order_by(BackfillRecord.last_attempt_at.desc(), MovieTrailer.created_at.desc())
        .all()
    )
    cohort = []
    seen_movies = set()
    for trailer, movie, record in rows:
        if movie.id in seen_movies:
            continue
        seen_movies.add(movie.id)
        cohort.append((trailer, movie, record))
        if len(cohort) >= cohort_size:
            break
    youtube = YouTubeTrailerService()
    metadata: dict[str, dict] = {}
    keys = [trailer.video_key for trailer, _, _ in cohort]
    for offset in range(0, len(keys), 50):
        response = youtube._request(
            "videos",
            {
                "part": "snippet,status,contentDetails",
                "id": ",".join(keys[offset : offset + 50]),
                "key": settings.YOUTUBE_API_KEY,
            },
        )
        metadata.update({item["id"]: item for item in response.json().get("items", [])})
    items = []
    auto_removed = restored = 0
    auto_categories = {
        "WRONG_MOVIE", "WRONG_YEAR_OR_REMAKE", "FAN_MADE", "REACTION_OR_REVIEW",
        "SONG_OR_MUSIC_VIDEO", "MOVIE_SCENE_OR_CLIP", "UNAVAILABLE_OR_DELETED",
    }
    for trailer, movie, record in cohort:
        video = metadata.get(trailer.video_key)
        category, confidence, reason = classify(movie, trailer, video)
        restored_id = None
        removed = False
        if args.apply and confidence == "HIGH" and category in auto_categories:
            trailer.is_primary = False
            if category == "UNAVAILABLE_OR_DELETED":
                trailer.is_unavailable = True
            replacement = (
                db.query(MovieTrailer)
                .filter(
                    MovieTrailer.movie_id == movie.id,
                    MovieTrailer.id != trailer.id,
                    MovieTrailer.is_unavailable.is_(False),
                )
                .order_by(MovieTrailer.official.desc(), MovieTrailer.created_at.asc())
                .first()
            )
            if replacement:
                replacement.is_primary = True
                restored_id = replacement.id
                restored += 1
            removed = True
            auto_removed += 1
        snippet = (video or {}).get("snippet") or {}
        items.append({
            "movie_id": movie.id,
            "movie_title": movie.title,
            "movie_original_title": movie.original_title,
            "movie_year": movie.release_date.year if movie.release_date else None,
            "movie_language": movie.original_language,
            "youtube_video_id": trailer.video_key,
            "youtube_title": snippet.get("title") or trailer.name,
            "youtube_channel": snippet.get("channelTitle"),
            "youtube_published_at": snippet.get("publishedAt"),
            "duration_seconds": duration_seconds(((video or {}).get("contentDetails") or {}).get("duration")),
            "embeddable": ((video or {}).get("status") or {}).get("embeddable"),
            "current_url": f"https://www.youtube.com/watch?v={trailer.video_key}",
            "classification": category,
            "confidence": confidence,
            "reason": reason,
            "auto_removed": removed,
            "restored_trailer_id": restored_id,
            "backfill_last_attempt_at": record.last_attempt_at.isoformat() if record.last_attempt_at else None,
        })
    if args.apply:
        db.commit()
    counts = Counter(item["classification"] for item in items)
    auto_approved = {
        "VALID_OFFICIAL_TRAILER", "VALID_OFFICIAL_TEASER",
        "VALID_DISTRIBUTOR_UPLOAD", "LIKELY_VALID",
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": "latest distinct primary-trailer movies matched to trailer-backfill attempts within 30 seconds",
        "operation_counter": cohort_size,
        "audited_count": len(items),
        "applied": args.apply,
        "counts": dict(sorted(counts.items())),
        "auto_removed_invalid": auto_removed,
        "restored_previous_trailers": restored,
        "review_queue_count": sum(
            1 for item in items
            if not item["auto_removed"] and item["classification"] not in auto_approved
        ),
        "items": items,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "items"}, indent=2))
    db.close()


if __name__ == "__main__":
    main()
