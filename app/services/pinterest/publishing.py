"""Pinterest publishing pipeline with duplicate protection and rate limiting."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.movie import Movie
from app.models.movie_metadata import MovieImage, MovieRating
from app.models.ott_availability import OttAvailability
from app.models.pinterest import PinterestBoard, PinterestPin
from app.services.pinterest.client import PinterestClient, PinterestAPIError
from app.services.pinterest.pin_generator import generate_pin, generate_branded_only_pin, TEMPLATES

logger = logging.getLogger(__name__)

# ── Pin type determination ─────────────────────────────────────────────

def determine_pin_type(movie: Movie) -> str:
    """Determine the appropriate pin type based on movie status."""
    if not movie.release_status_code:
        return "MOVIE_DISCOVERY"

    status = movie.release_status_code.upper()
    today = date.today()

    # Check for OTT availability
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
            latest_ott = max(ott_dates)
            if latest_ott <= today:
                return "NOW_STREAMING"
            elif (latest_ott - today).days <= 14:
                return "THIS_WEEK_ON_OTT"
        return "OTT_RELEASE"

    if status in ("UPCOMING", "ANNOUNCED"):
        return "COMING_SOON"

    if status in ("RELEASED", "IN_THEATERS"):
        vote = movie.vote_average or 0
        if vote >= 7.5:
            return "HIGHLY_RATED"
        return "MOVIE_DISCOVERY"

    return "MOVIE_DISCOVERY"


def determine_template(pin_type: str) -> str:
    """Map pin type to template name."""
    mapping = {
        "NOW_STREAMING": "NOW_STREAMING",
        "OTT_RELEASE": "OTT_RELEASE",
        "COMING_SOON": "COMING_SOON",
        "MOVIE_DISCOVERY": "MOVIE_DISCOVERY",
        "HIGHLY_RATED": "HIGHLY_RATED",
        "THIS_WEEK_ON_OTT": "THIS_WEEK_ON_OTT",
    }
    return mapping.get(pin_type, "MOVIE_DISCOVERY")


# ── Content generation ─────────────────────────────────────────────────

def _best_poster_path(movie: Movie, db: Session) -> str | None:
    """Find the best local poster artwork for a movie."""
    image = (
        db.query(MovieImage)
        .filter(
            MovieImage.movie_id == movie.id,
            MovieImage.image_type == "poster",
            MovieImage.local_path.isnot(None),
            MovieImage.is_primary == True,  # noqa: E712
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


def _best_backdrop_path(movie: Movie, db: Session) -> str | None:
    """Find the best local backdrop artwork for a movie."""
    image = (
        db.query(MovieImage)
        .filter(
            MovieImage.movie_id == movie.id,
            MovieImage.image_type == "backdrop",
            MovieImage.local_path.isnot(None),
            MovieImage.is_primary == True,  # noqa: E712
        )
        .first()
    )
    if not image:
        image = (
            db.query(MovieImage)
            .filter(
                MovieImage.movie_id == movie.id,
                MovieImage.image_type == "backdrop",
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


def _get_ott_info(movie: Movie) -> tuple[str | None, str | None]:
    """Get the best OTT platform and release date for a movie."""
    if not movie.ott_availabilities:
        return None, None

    best = None
    for avail in movie.ott_availabilities:
        if avail.status in ("available", "confirmed"):
            if best is None or (avail.ott_release_date and (best.ott_release_date is None or avail.ott_release_date > best.ott_release_date)):
                best = avail
    if best:
        return best.provider, best.ott_release_date.isoformat() if best.ott_release_date else None

    # Fallback to any availability
    for avail in movie.ott_availabilities:
        if avail.provider:
            return avail.provider, avail.ott_release_date.isoformat() if avail.ott_release_date else None

    return None, None


def _get_genres(movie: Movie) -> list[str]:
    """Get genre names for a movie."""
    if not movie.genres:
        return []
    return [g.name for g in movie.genres[:5]]


def build_movie_dict(movie: Movie, db: Session) -> dict:
    """Build a movie data dict for pin generation."""
    ott_platform, ott_date = _get_ott_info(movie)
    year = movie.release_date.year if movie.release_date else None
    return {
        "id": movie.id,
        "title": movie.title,
        "original_title": movie.original_title,
        "original_language": movie.original_language,
        "release_date": year,
        "genres": _get_genres(movie),
        "ott_platform": ott_platform,
        "ott_release_date": ott_date,
        "vote_average": movie.vote_average,
    }


# ── SEO content generation ─────────────────────────────────────────────

def generate_pin_title(movie: Movie, pin_type: str) -> str:
    """Generate an SEO-friendly Pinterest pin title."""
    title = movie.title
    year = movie.release_date.year if movie.release_date else ""
    lang = movie.original_language or ""

    if pin_type == "NOW_STREAMING":
        return f"{title} ({year}) — Now Streaming on OTT"
    elif pin_type == "OTT_RELEASE":
        return f"{title} OTT Release — Streaming Details"
    elif pin_type == "COMING_SOON":
        return f"{title} ({year}) — Coming Soon to OTT"
    elif pin_type == "HIGHLY_RATED":
        return f"{title} ({year}) — Highly Rated {lang.upper()} Film"
    elif pin_type == "THIS_WEEK_ON_OTT":
        return f"{title} — This Week on OTT"
    else:
        return f"{title} ({year}) — Where to Watch Online"


def generate_pin_description(movie: Movie, pin_type: str) -> str:
    """Generate a concise, useful Pinterest description from verified data only."""
    parts = []
    title = movie.title
    year = movie.release_date.year if movie.release_date else None
    lang = movie.original_language or ""

    if year:
        parts.append(f"{title} ({year})")
    else:
        parts.append(title)

    genres = _get_genres(movie)
    if lang:
        lang_name = {"ml": "Malayalam", "ta": "Tamil", "te": "Telugu", "hi": "Hindi", "kn": "Kannada", "en": "English", "ko": "Korean", "ja": "Japanese"}.get(lang, lang.upper())
        genre_str = f"{lang_name} {' '.join(genres[:2])} film" if genres else f"{lang_name} film"
        parts.append(f"is a {genre_str}.")
    elif genres:
        parts.append(f"is a {' '.join(genres[:2])} film.")

    ott_platform, ott_date = _get_ott_info(movie)
    if ott_platform:
        if ott_date:
            parts.append(f"Streaming on {ott_platform} from {ott_date}.")
        else:
            parts.append(f"Streaming on {ott_platform}.")

    parts.append("Find where to watch on OTTTracker.")
    return " ".join(parts)


def generate_destination_url(movie: Movie) -> str:
    """Generate the canonical OTTTracker movie detail URL."""
    base = settings.SITE_URL.rstrip("/")
    return f"{base}/movies/{movie.id}"


# ── Duplicate protection ───────────────────────────────────────────────

def has_existing_pin(movie_id: int, pin_type: str, platform: str | None, db: Session) -> bool:
    """Check if a pin already exists for this movie/type/platform combination."""
    query = db.query(PinterestPin).filter(
        PinterestPin.movie_id == movie_id,
        PinterestPin.pin_type == pin_type,
        PinterestPin.status.in_(["PUBLISHED", "READY_FOR_REVIEW", "APPROVED", "PUBLISHING"]),
    )
    if platform:
        query = query.filter(PinterestPin.platform == platform)
    else:
        query = query.filter(PinterestPin.platform.is_(None))
    return query.first() is not None


# ── Pin creation pipeline ──────────────────────────────────────────────

def create_pin_candidate(
    movie: Movie,
    db: Session,
    force_template: str | None = None,
) -> PinterestPin | None:
    """Create a pin candidate for a movie. Returns None if duplicate or ineligible."""
    pin_type = determine_pin_type(movie)
    template = force_template or determine_template(pin_type)

    # Duplicate check
    ott_platform, _ = _get_ott_info(movie)
    if has_existing_pin(movie.id, pin_type, ott_platform, db):
        logger.info("Skipping pin for movie %s: duplicate %s", movie.id, pin_type)
        return None

    # Build destination URL
    dest_url = generate_destination_url(movie)

    # Determine artwork
    poster_path = _best_poster_path(movie, db)
    backdrop_path = _best_backdrop_path(movie, db)

    artwork_path = poster_path or backdrop_path
    artwork_source = "poster" if poster_path else ("backdrop" if backdrop_path else None)
    rights_status = "RIGHTS_UNKNOWN"

    # Generate pin content
    pin_title = generate_pin_title(movie, pin_type)
    pin_desc = generate_pin_description(movie, pin_type)

    # Create DB record
    pin = PinterestPin(
        movie_id=movie.id,
        pin_type=pin_type,
        template=template,
        pin_title=pin_title,
        pin_description=pin_desc,
        destination_url=dest_url,
        artwork_source=artwork_source,
        artwork_rights_status=rights_status,
        platform=ott_platform,
        status="PENDING",
    )
    db.add(pin)
    db.flush()

    # Generate the image
    try:
        movie_dict = build_movie_dict(movie, db)
        if artwork_path and artwork_source in ("poster", "backdrop"):
            filepath, rel_path = generate_pin(
                movie=movie_dict,
                template_name=template,
                artwork_path=artwork_path,
                artwork_source=artwork_source,
            )
        else:
            filepath, rel_path = generate_branded_only_pin(
                movie=movie_dict,
                template_name=template,
            )
            pin.artwork_source = "branded_only"
            pin.artwork_rights_status = "BRANDED_ONLY"

        pin.image_path = rel_path
        pin.status = "READY_FOR_REVIEW"
        pin.generated_at = datetime.now(timezone.utc)
    except Exception as exc:
        logger.error("Failed to generate pin for movie %s: %s", movie.id, exc)
        pin.status = "FAILED"
        pin.last_error = str(exc)[:500]

    db.commit()
    return pin


# ── Pinterest publishing ───────────────────────────────────────────────

def publish_pin(pin: PinterestPin, db: Session) -> bool:
    """Publish a pin to Pinterest. Returns True on success."""
    if not settings.PINTEREST_ACCESS_TOKEN:
        pin.status = "FAILED"
        pin.last_error = "No Pinterest access token configured"
        db.commit()
        return False

    if not pin.image_path:
        pin.status = "FAILED"
        pin.last_error = "No image generated"
        db.commit()
        return False

    # Find or use the assigned board
    if not pin.board_id:
        pin.status = "FAILED"
        pin.last_error = "No board assigned"
        db.commit()
        return False

    board = db.query(PinterestBoard).filter_by(id=pin.board_id).first()
    if not board:
        pin.status = "FAILED"
        pin.last_error = "Board not found"
        db.commit()
        return False

    client = PinterestClient()

    # Build image URL (must be publicly accessible)
    image_url = f"{settings.SITE_URL.rstrip('/')}{pin.image_path}"

    pin.status = "PUBLISHING"
    db.commit()

    try:
        result = client.create_pin(
            board_id=board.pinterest_board_id,
            title=pin.pin_title,
            description=pin.pin_description,
            link=pin.destination_url,
            image_url=image_url,
            alt_text=f"OTTTracker pin for {pin.movie.title if pin.movie else 'movie'}",
        )
        pin.pinterest_pin_id = result.get("id")
        pin.pinterest_pin_url = f"https://pinterest.com/pin/{pin.pinterest_pin_id}" if pin.pinterest_pin_id else None
        pin.status = "PUBLISHED"
        pin.published_at = datetime.now(timezone.utc)
        pin.last_error = None
        db.commit()
        logger.info("Published pin %s for movie %s", pin.pinterest_pin_id, pin.movie_id)
        return True
    except PinterestAPIError as exc:
        pin.status = "FAILED"
        pin.last_error = f"{exc.status_code}: {exc.detail[:400]}"
        pin.retry_count += 1
        db.commit()
        logger.error("Failed to publish pin %s: %s", pin.id, exc)
        return False
    except Exception as exc:
        pin.status = "FAILED"
        pin.last_error = str(exc)[:500]
        pin.retry_count += 1
        db.commit()
        logger.error("Unexpected error publishing pin %s: %s", pin.id, exc)
        return False
