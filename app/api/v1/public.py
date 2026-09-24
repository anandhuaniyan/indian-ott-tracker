"""Public discovery APIs for the movie-only V1 experience."""

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Integer, and_, case, cast, exists, func, or_, select, union_all, literal
from sqlalchemy.orm import Session, aliased, selectinload

from app.database.connection import get_db
from app.config.settings import settings
from app.models.genre import Genre
from app.models.language import Language
from app.models.movie import Movie
from app.models.community_rating import CommunityRating, User
from app.models.movie_metadata import (
    AlternativeTitle,
    ExternalId,
    Keyword,
    MovieCredit,
    MovieImage,
    MovieKeyword,
    MovieProductionCompany,
    MovieProductionCountry,
    MovieRating,
    MovieReleaseDate,
    Person,
    ProductionCompany,
    ProductionCountry,
)
from app.models.ott_availability import OttAvailability
from app.models.operations import MovieComment, OttEvidence, OttSourceRelease
from app.models.editorial import EditorialArticle, MovieCorrection
from app.core.rate_limit import limit
from app.core.user_auth import require_user, require_user_same_origin
from app.services.release_status import (
    ReleaseStatusService,
    best_canonical_ott,
    classify_release,
    confirmed_canonical_ott,
    site_date,
)
from app.services.roles import ROLE_ALIASES, normalize_role
from app.services.languages import (
    INDIAN_PRIMARY_LANGUAGE_CODES,
    LANGUAGE_CONFIG,
    INDIAN_LANGUAGE_CODES,
    INTERNATIONAL_LANGUAGE_CODES,
    SUPPORTED_PRIMARY_LANGUAGE_CODES,
    language_filter_codes,
    language_name,
    normalize_language_code,
)
from app.services.ott_providers import PUBLIC_OTT_STATES, normalize_platform, platform_slug
from app.services.trailers import TrailerService, trailer_payload, trailer_videos_payload
from app.services.maintenance import public_maintenance_snapshot
from app.services.content_quality import evaluate_movie_ads_quality

router = APIRouter(prefix="/api/v1", tags=["Discovery"])
THEATRICAL_RELEASE_TYPES = ("2", "3", "limited theatrical", "theatrical")
COMMENT_STATUSES = {"PENDING", "APPROVED", "HIDDEN", "REJECTED"}
CORRECTION_CATEGORIES = {"WRONG_OTT_PLATFORM", "WRONG_OTT_DATE", "BROKEN_STREAMING_LINK", "INCORRECT_POSTER", "INCORRECT_METADATA", "OTHER"}


class CorrectionSubmission(BaseModel):
    category: str
    message: str = Field(min_length=10, max_length=3000)
    email: str | None = Field(default=None, max_length=320)

    @field_validator("category")
    @classmethod
    def valid_category(cls, value):
        normalized = value.strip().upper()
        if normalized not in CORRECTION_CATEGORIES:
            raise ValueError("Unsupported correction category")
        return normalized


def _published_articles(db: Session):
    return db.query(EditorialArticle).filter(
        EditorialArticle.status == "PUBLISHED",
        EditorialArticle.reviewed_at.is_not(None),
        EditorialArticle.published_at.is_not(None),
    )


def _article_payload(article, *, include_body=False):
    payload = {key: getattr(article, key) for key in (
        "slug", "title", "summary", "content_type", "author_name", "reviewed_at",
        "published_at", "updated_at", "seo_title", "seo_description", "hero_image",
        "is_indexable", "ads_eligible",
    )}
    if include_body:
        payload["body"] = article.body
    return payload


@router.get("/maintenance")
def maintenance_status(db: Session = Depends(get_db)):
    """Return public, read-only maintenance status and recent history."""
    return public_maintenance_snapshot(db)

# Local search relevance.
#
# Normal movie search is title-centric and deterministic: exact titles rank
# above prefixes, prefixes above substrings, substrings above a trigram fuzzy
# tier.  Trigrams are only consulted once the query is long enough to be
# meaningful, and only matches at or above SEARCH_FUZZY_THRESHOLD survive.
# The trigram layer exists purely for typo tolerance (vazha/vaaza -> Vaazha),
# not to pad results: movies that merely share an actor, keyword, or a loose
# alternative-title word are intentionally excluded from normal search.
SEARCH_FUZZY_THRESHOLD = 0.40
SEARCH_MIN_TRIGRAM_LENGTH = 3
SEARCH_STRONG_FUZZY_THRESHOLD = 0.60
SEARCH_FUZZY_MAX_GAP = 0.15


class CommentSubmission(BaseModel):
    display_name: str = Field(min_length=2, max_length=50)
    comment: str = Field(min_length=2, max_length=2000)
    email: str | None = Field(default=None, max_length=320)

    @field_validator("display_name", "comment")
    @classmethod
    def clean_required(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("This field must contain at least 2 characters")
        return value

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str | None) -> str | None:
        if not value:
            return None
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("A valid email address is required")
        return value


class CommunityRatingSubmission(BaseModel):
    rating: float = Field(ge=1, le=10)

    @field_validator("rating")
    @classmethod
    def half_point_increment(cls, value: float) -> float:
        if abs(value * 2 - round(value * 2)) > 1e-9:
            raise ValueError("Rating must use 0.5 increments")
        return value


def _community_rating_summary(db: Session, movie_id: int) -> dict:
    average, count = db.query(
        func.avg(CommunityRating.rating), func.count(CommunityRating.id)
    ).filter(CommunityRating.movie_id == movie_id).one()
    return {
        "average": round(float(average), 1) if average is not None else None,
        "count": int(count or 0),
    }


def _is_metadata_provider(value: str | None) -> bool:
    normalized = re.sub(r"[^a-z]", "", (value or "").lower())
    return normalized in {"tmdb", "themoviedb", "themoviedatabase"}


def _stored_rating(movie: Movie, source: str) -> MovieRating | None:
    return next(
        (item for item in movie.ratings if item.source.lower() == source.lower()), None
    )


def _imdb_rating_query():
    return (
        select(MovieRating.rating)
        .where(
            MovieRating.movie_id == Movie.id, func.lower(MovieRating.source) == "imdb"
        )
        .limit(1)
        .correlate(Movie)
        .scalar_subquery()
    )


def _primary_release_date_query():
    return func.coalesce(Movie.theatrical_release_date, Movie.release_date)


def _provider_aliases(db: Session, value: str) -> tuple[str, list[str]]:
    canonical = normalize_platform(value.replace("-", " ")) or value
    stored = [row[0] for row in db.query(OttAvailability.provider).distinct().all()]
    aliases = [item for item in stored if normalize_platform(item) == canonical]
    return canonical, aliases or [value.replace("-", " ")]


def _platform_catalog(db: Session, *, public_only: bool = False) -> list[dict]:
    query = db.query(
        OttAvailability.provider,
        OttAvailability.provider_logo,
        OttAvailability.movie_id,
    )
    query = query.filter(func.upper(OttAvailability.country) == "IN")
    if public_only:
        query = query.join(Movie, Movie.id == OttAvailability.movie_id).filter(
            OttAvailability.status.in_(PUBLIC_OTT_STATES),
            Movie.safety_public_visibility == "FULL",
        )
    grouped: dict[str, dict] = {}
    for provider, logo, movie_id in query.all():
        canonical = normalize_platform(provider)
        if not canonical or not platform_slug(canonical):
            continue
        item = grouped.setdefault(
            canonical, {"name": canonical, "logo": None, "movie_ids": set()}
        )
        item["logo"] = item["logo"] or logo
        item["movie_ids"].add(movie_id)
    return sorted(
        (
            {
                "slug": platform_slug(item["name"]),
                "name": item["name"],
                "logo": item["logo"],
                "movie_count": len(item["movie_ids"]),
            }
            for item in grouped.values()
        ),
        key=lambda item: (-item["movie_count"], item["name"].lower()),
    )


def _canonical_ott_rows(movie: Movie) -> list[OttAvailability]:
    """Collapse stored aliases at the read boundary without deleting history."""
    selected: dict[tuple[str, str, str], OttAvailability] = {}
    for row in movie.ott_availabilities:
        if (row.country or "").upper() != "IN":
            continue
        provider = normalize_platform(row.provider)
        if not provider:
            continue
        key = (provider, row.country, row.watch_type)
        current = selected.get(key)
        score = (
            row.verification_status == "CONFIRMED",
            row.manually_verified,
            row.ott_release_date is not None,
            row.source_type == "official_platform",
            row.updated_at or row.created_at,
        )
        if current is None:
            selected[key] = row
            continue
        current_score = (
            current.verification_status == "CONFIRMED",
            current.manually_verified,
            current.ott_release_date is not None,
            current.source_type == "official_platform",
            current.updated_at or current.created_at,
        )
        if score > current_score:
            selected[key] = row
    return sorted(selected.values(), key=lambda row: normalize_platform(row.provider))


def _card(movie: Movie) -> dict:
    imdb = _stored_rating(movie, "imdb")
    imdb_id = next(
        (
            item.external_id
            for item in movie.external_ids
            if item.provider.lower() == "imdb"
        ),
        None,
    )
    rating_status = (
        "available"
        if imdb and imdb.rating is not None
        else (
            "pending"
            if imdb_id or (imdb and imdb.status not in {"INVALID_ID"})
            else "unavailable"
        )
    )
    stored_language = next(
        (
            item.english_name
            for item in movie.languages
            if item.iso_639_1 == movie.original_language
        ),
        None,
    )
    confirmed_ott = confirmed_canonical_ott(movie)
    platforms = sorted({normalize_platform(x.provider) for x in movie.ott_availabilities
                        if x.status in PUBLIC_OTT_STATES and normalize_platform(x.provider)})
    return {
        "id": movie.id,
        "title": movie.title,
        "original_title": movie.original_title,
        "overview": movie.overview,
        "release_date": movie.theatrical_release_date or movie.release_date,
        "theatrical_release_date": movie.theatrical_release_date,
        "poster_path": movie.poster_path,
        "backdrop_path": movie.backdrop_path,
        "rating": imdb.rating if imdb else None,
        "rating_source": "IMDb" if imdb and imdb.rating is not None else None,
        "rating_status": rating_status,
        "vote_count": imdb.vote_count if imdb else None,
        "popularity": movie.popularity,
        "language": movie.original_language,
        "language_name": language_name(movie.original_language, stored_language),
        "genres": [g.name for g in movie.genres],
        "ott_platform": ", ".join(platforms) or None,
        "ott_platforms": platforms,
        "ott_release_date": confirmed_ott.ott_release_date if confirmed_ott else None,
        "ott_state": confirmed_ott.status.upper() if confirmed_ott else None,
    }


def movie_identity(movie):
    """Provider-independent identity; never merge conflicting primary IDs."""
    if movie.tmdb_id:
        return ("tmdb", movie.tmdb_id)
    imdb = next((x.external_id.strip().lower() for x in movie.external_ids
                 if x.provider.lower() == "imdb" and x.external_id), None)
    if imdb:
        return ("imdb", imdb)
    title = "".join(c for c in movie.title.casefold() if c.isalnum())
    year = movie.release_date.year if movie.release_date else None
    if title and year and movie.original_language:
        return ("title", title, year, movie.original_language.lower())
    return ("local", movie.id)


def cards(items) -> list[dict]:
    selected = {}
    for movie in items:
        selected.setdefault(movie_identity(movie), movie)
    return [_card(movie) for movie in selected.values()]


def _cast_payload(credits):
    selected = {}
    for credit in credits:
        if credit.credit_type != "cast":
            continue
        person = credit.person
        key = ("tmdb", person.tmdb_id) if person.tmdb_id else (
            ("imdb", person.imdb_id) if person.imdb_id else ("local", person.id))
        item = selected.setdefault(key, {
            "person_id": person.id, "name": person.name,
            "profile_path": person.profile_path, "character": "",
            "order": credit.cast_order, "characters": [],
        })
        character = (credit.character or "").strip()
        if character and character.casefold() not in {x.casefold() for x in item["characters"]}:
            item["characters"].append(character)
        item["character"] = " / ".join(item["characters"])
        if credit.cast_order is not None:
            item["order"] = min(item["order"], credit.cast_order) if item["order"] is not None else credit.cast_order
    return sorted(selected.values(), key=lambda x: (x["order"] is None, x["order"] or 0, x["person_id"]))


def _external_id_payload(item: ExternalId) -> dict:
    provider = item.provider.strip()
    url = item.source_url
    if provider.lower() == "imdb" and re.fullmatch(r"tt\d{7,10}", item.external_id):
        provider = "IMDb"
        url = f"https://www.imdb.com/title/{item.external_id}/"
    return {"provider": provider, "id": item.external_id, "url": url}


def _queue_on_demand_repair(
    db: Session, movie: Movie, credits: list[MovieCredit]
) -> bool:
    """Compatibility no-op: public reads must never schedule repair work."""
    return False


def _ratings_payload(movie: Movie) -> list[dict]:
    imdb_id = next(
        (
            item.external_id
            for item in movie.external_ids
            if item.provider.lower() == "imdb"
        ),
        None,
    )
    payload = []
    has_source_rating = False
    for item in movie.ratings:
        source = (
            "IMDb"
            if item.source.lower() == "imdb"
            else "Source Rating" if _is_metadata_provider(item.source) else item.source
        )
        has_source_rating = has_source_rating or source == "Source Rating"
        payload.append(
            {
                "source": source,
                "rating": item.rating,
                "votes": item.vote_count,
                "status": "available" if item.rating is not None else "pending",
                "source_id": imdb_id if source == "IMDb" else None,
                "checked_at": item.last_updated_at,
            }
        )
    if movie.vote_average is not None and not has_source_rating:
        payload.append(
            {
                "source": "Source Rating",
                "rating": movie.vote_average,
                "votes": movie.vote_count,
                "source_id": None,
                "checked_at": None,
            }
        )
    return sorted(payload, key=lambda item: (item["source"] != "IMDb", item["source"]))


def _movie_query(db: Session):
    return db.query(Movie).filter(
        Movie.safety_public_visibility == "FULL"
    ).options(
        selectinload(Movie.genres),
        selectinload(Movie.languages),
        selectinload(Movie.ratings),
        selectinload(Movie.external_ids),
        selectinload(Movie.ott_availabilities),
    )


def _credit_match(role: str, person_value: str):
    credit = aliased(MovieCredit)
    person = aliased(Person)
    normalized = normalize_role(role)
    values = ROLE_ALIASES.get(normalized, (normalized,))
    role_match = (
        credit.credit_type == "cast"
        if normalized == "actor"
        else or_(
            func.lower(credit.job).in_(values),
            func.lower(credit.department).in_(values),
        )
    )
    identity = (
        credit.person_id == int(person_value)
        if person_value.isdigit()
        else person.name.ilike(f"%{person_value.strip()}%")
    )
    return exists(
        select(credit.id)
        .join(person, person.id == credit.person_id)
        .where(credit.movie_id == Movie.id, role_match, identity)
    )


def _apply_language_filter(query, value: str | None):
    try:
        codes = language_filter_codes(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if codes:
        query = query.filter(Movie.original_language.in_(codes))
    return query


def _language_counts(query) -> dict[str, int]:
    """Count distinct movies per global language tab within a query context."""
    source = (
        query.enable_eagerloads(False)
        .order_by(None)
        .with_entities(
            Movie.id.label("movie_id"),
            Movie.original_language.label("language"),
        )
        .distinct()
        .subquery()
    )
    rows = (
        query.session.query(source.c.language, func.count(source.c.movie_id))
        .group_by(source.c.language)
        .all()
    )
    raw = {code: int(count) for code, count in rows}
    return {
        "all": sum(raw.values()),
        "indian": sum(raw.get(code, 0) for code in INDIAN_PRIMARY_LANGUAGE_CODES),
        **{code: raw.get(code, 0) for code in SUPPORTED_PRIMARY_LANGUAGE_CODES},
    }


def _empty_language_counts() -> dict[str, int]:
    return {
        "all": 0,
        "indian": 0,
        **{code: 0 for code in SUPPORTED_PRIMARY_LANGUAGE_CODES},
    }


def _apply_filters(query, **values):
    q = values.get("q")
    if q:
        term = f"%{q.strip()}%"
        credit_match = exists(
            select(MovieCredit.id)
            .join(Person)
            .where(MovieCredit.movie_id == Movie.id, Person.name.ilike(term))
        )
        keyword_match = exists(
            select(MovieKeyword.movie_id)
            .join(Keyword)
            .where(MovieKeyword.movie_id == Movie.id, Keyword.name.ilike(term))
        )
        alternative_match = exists(
            select(AlternativeTitle.id).where(
                AlternativeTitle.movie_id == Movie.id,
                AlternativeTitle.title.ilike(term),
            )
        )
        query = query.filter(
            or_(
                Movie.title.ilike(term),
                Movie.original_title.ilike(term),
                alternative_match,
                credit_match,
                keyword_match,
            )
        )
    # Public language filters and /languages/:code pages represent a movie's
    # original language. Spoken-language associations must never affect them.
    query = _apply_language_filter(query, values.get("language"))
    language_region = (values.get("language_region") or "all").strip().lower()
    if language_region == "indian":
        query = query.filter(Movie.original_language.in_(INDIAN_LANGUAGE_CODES))
    elif language_region == "international":
        query = query.filter(Movie.original_language.in_(INTERNATIONAL_LANGUAGE_CODES))
    elif language_region != "all":
        raise HTTPException(422, "Unknown language region")
    if values.get("genre"):
        query = query.filter(Movie.genres.any(Genre.slug == values["genre"]))
    if values.get("year"):
        query = query.filter(
            func.extract("year", _primary_release_date_query()) == values["year"]
        )
    if values.get("rating") is not None:
        query = query.filter(_imdb_rating_query() >= values["rating"])
    if values.get("certification"):
        query = query.filter(
            Movie.release_dates.any(
                MovieReleaseDate.certification == values["certification"]
            )
        )
    if values.get("release_status"):
        status = values["release_status"].strip().lower().replace("_", "-")
        code = {
            "released": "THEATRICALLY_RELEASED",
            "theatrically-released": "THEATRICALLY_RELEASED",
            "upcoming": "UPCOMING",
            "direct-to-ott": "DIRECT_TO_OTT",
            "unknown": "UNKNOWN",
        }.get(status)
        if not code:
            raise HTTPException(422, "Unknown release status")
        query = query.filter(Movie.release_status_code == code)
    if values.get("platform"):
        _, aliases = _provider_aliases(query.session, values["platform"])
        query = query.filter(
            Movie.ott_availabilities.any(
                and_(
                    OttAvailability.provider.in_(aliases),
                    func.upper(OttAvailability.country) == "IN",
                )
            )
        )
    role_filters = {
        "actor": values.get("actor"),
        "director": values.get("director"),
        "writer": values.get("writer"),
        "cinematography": values.get("cinematographer"),
        "producer": values.get("producer"),
        "editor": values.get("editor"),
        "composer": values.get("composer"),
    }
    for role_name, person_value in role_filters.items():
        if person_value:
            query = query.filter(_credit_match(role_name, person_value))
    if values.get("person"):
        query = (
            query.filter(
                _credit_match(values.get("role") or "actor", str(values["person"]))
            )
            if values.get("role")
            else query.filter(
                Movie.credits.any(MovieCredit.person_id == values["person"])
            )
        )
    if values.get("date_from"):
        query = query.filter(_primary_release_date_query() >= values["date_from"])
    if values.get("date_to"):
        query = query.filter(_primary_release_date_query() <= values["date_to"])
    return query


def _ordering(sort: str):
    ott_date = (
        select(func.max(OttAvailability.ott_release_date))
        .where(
            OttAvailability.movie_id == Movie.id,
            OttAvailability.verification_status == "CONFIRMED",
            func.upper(OttAvailability.country) == "IN",
        )
        .correlate(Movie)
        .scalar_subquery()
    )
    return {
        "latest": _primary_release_date_query().desc(),
        "oldest": _primary_release_date_query().asc(),
        "rating": _imdb_rating_query().desc(),
        "highest-rated": _imdb_rating_query().desc(),
        "popular": Movie.popularity.desc(),
        "popularity": Movie.popularity.desc(),
        "recent": Movie.created_at.desc(),
        "recently-added": Movie.created_at.desc(),
        "ott-release": ott_date.desc(),
        "name-asc": Movie.title.asc(),
        "name-desc": Movie.title.desc(),
    }.get(sort, _primary_release_date_query().desc())


def _section_listing(query, section: str | None):
    """Keep homepage View More results within their originating data section."""
    if not section or section in {"popular", "recently-added", "language"}:
        return query
    today = site_date()
    primary_date = _primary_release_date_query()
    if section == "latest-theatrical":
        return query.filter(primary_date <= today)
    if section == "upcoming-theatrical":
        return query.filter(primary_date > today)
    if section in {"upcoming-ott", "recent-ott"}:
        date_filter = (
            OttAvailability.ott_release_date > today
            if section == "upcoming-ott"
            else OttAvailability.ott_release_date <= today
        )
        return query.filter(
            Movie.ott_availabilities.any(
                and_(
                    OttAvailability.verification_status == "CONFIRMED",
                    func.upper(OttAvailability.country) == "IN",
                    OttAvailability.status.in_(("upcoming", "released")),
                    date_filter,
                )
            )
        )
    return query


def _page(query, sort: str, page: int, page_size: int):
    total = query.order_by(None).count()
    items = (
        query.order_by(_ordering(sort), Movie.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": cards(items),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


def _public_languages(db: Session) -> list[dict]:
    """Return only non-empty public language pages in stable editorial order."""
    enabled = tuple(item for item in LANGUAGE_CONFIG if item.enabled)
    counts = dict(
        db.query(Movie.original_language, func.count(Movie.id))
        .filter(
            Movie.original_language.in_(tuple(item.code for item in enabled)),
            Movie.safety_public_visibility == "FULL",
        )
        .group_by(Movie.original_language)
        .all()
    )
    items = []
    for definition in enabled:
        count = int(counts.get(definition.code) or 0)
        if not count:
            continue
        items.append(
            {
                "code": definition.code,
                "name": definition.display_name,
                "slug": definition.slug,
                "region": definition.group,
                "group": definition.tier,
                "enabled": definition.enabled,
                "movie_count": count,
            }
        )
    return items


def _public_language_groups(db: Session) -> dict:
    items = _public_languages(db)
    return {
        "indian": {
            "primary": [item for item in items if item["region"] == "indian" and item["group"] == "primary"],
            "more": [item for item in items if item["region"] == "indian" and item["group"] == "more"],
        },
        "international": {
            "primary": [item for item in items if item["region"] == "international" and item["group"] == "primary"],
            "more": [item for item in items if item["region"] == "international" and item["group"] == "more"],
        },
    }


@router.get("/home")
def home(language: str | None = None, db: Session = Depends(get_db)):
    # One global selection controls every movie rail. Counts deliberately come
    # from the unfiltered catalogue so users can see useful alternatives.
    unfiltered = _movie_query(db)
    language_counts = _language_counts(unfiltered)
    base = lambda: _apply_language_filter(_movie_query(db), language)
    today = site_date()
    primary_date = _primary_release_date_query()
    ott_base = (
        lambda: base()
        .join(OttAvailability)
        .filter(
            OttAvailability.verification_status == "CONFIRMED",
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.status.in_(("upcoming", "released")),
        )
    )
    verified_since = datetime.now(timezone.utc) - timedelta(days=7)
    recent_verified_query = db.query(OttAvailability).join(Movie).filter(
        Movie.safety_public_visibility == "FULL",
        Movie.adult.is_(False),
        OttAvailability.verification_status == "CONFIRMED",
        OttAvailability.verified_at >= verified_since,
    )
    recently_verified = recent_verified_query.order_by(OttAvailability.verified_at.desc()).limit(6).all()
    return {
        "trending": cards(base().order_by(Movie.popularity.desc()).limit(12).all()),
        "popular": cards(
            base()
            .order_by(Movie.popularity.desc(), Movie.vote_count.desc())
            .limit(12)
            .all()
        ),
        "latest_theatrical": cards(
            base()
            .filter(primary_date <= today)
            .order_by(primary_date.desc())
            .limit(12)
            .all()
        ),
        "upcoming_theatrical": cards(
            base().filter(primary_date > today).order_by(primary_date).limit(12).all()
        ),
        "recently_added": cards(
            base().order_by(Movie.created_at.desc()).limit(12).all()
        ),
        "highest_rated": cards(
            base()
            .filter(_imdb_rating_query().is_not(None))
            .order_by(_imdb_rating_query().desc(), Movie.id.desc())
            .limit(12)
            .all()
        ),
        "upcoming_ott": cards(
            ott_base()
            .filter(OttAvailability.ott_release_date > today)
            .order_by(OttAvailability.ott_release_date)
            .limit(12)
            .all()
        ),
        "recent_ott": cards(
            ott_base()
            .filter(OttAvailability.ott_release_date <= today)
            .order_by(OttAvailability.ott_release_date.desc())
            .limit(12)
            .all()
        ),
        "language": normalize_language_code(language) or "all",
        "language_counts": language_counts,
        "language_groups": _public_language_groups(db),
        "languages": _public_languages(db),
        "genres": [
            {"slug": item.slug, "name": item.name}
            for item in db.query(Genre).order_by(Genre.name).all()
        ],
        "platforms": _platform_catalog(db),
        "verification_activity": {
            "titles_updated": recent_verified_query.with_entities(func.count(func.distinct(OttAvailability.movie_id))).scalar() or 0,
            "dates_confirmed": recent_verified_query.filter(OttAvailability.ott_release_date.is_not(None)).count(),
            "titles": cards([row.movie for row in recently_verified]),
            "period_days": 7,
        },
        "latest_updates": [_article_payload(row) for row in _published_articles(db).order_by(EditorialArticle.published_at.desc()).limit(3)],
    }


@router.get("/updates")
def updates(page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=50), db: Session = Depends(get_db)):
    query = _published_articles(db)
    total = query.count()
    rows = query.order_by(EditorialArticle.published_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_article_payload(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/updates/{slug}")
def update_article(slug: str, db: Session = Depends(get_db)):
    article = _published_articles(db).filter(EditorialArticle.slug == slug).first()
    if not article:
        raise HTTPException(404, "Update not found")
    return _article_payload(article, include_body=True)


@router.post("/movies/{movie_id}/corrections", status_code=201)
def submit_movie_correction(movie_id: int, payload: CorrectionSubmission, request: Request, db: Session = Depends(get_db)):
    limit(request, "movie-correction", 5, 3600, identity=movie_id)
    exists_movie = db.query(Movie.id).filter(Movie.id == movie_id, Movie.safety_public_visibility != "HIDDEN").first()
    if not exists_movie:
        raise HTTPException(404, "Movie not found")
    item = MovieCorrection(movie_id=movie_id, category=payload.category, message=payload.message.strip(), email=(payload.email or "").strip() or None)
    db.add(item)
    db.commit()
    return {"status": "received", "reference": item.id}


@router.get("/discover")
def discover(
    q: str | None = Query(None, max_length=200),
    language: str | None = None,
    language_region: str = Query("all", pattern="^(indian|international|all)$"),
    genre: str | None = None,
    person: int | None = None,
    role: str | None = None,
    year: int | None = None,
    rating: float | None = Query(None, ge=0, le=10),
    certification: str | None = None,
    release_status: str | None = None,
    platform: str | None = None,
    actor: str | None = None,
    director: str | None = None,
    writer: str | None = None,
    cinematographer: str | None = None,
    producer: str | None = None,
    editor: str | None = None,
    composer: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    section: str | None = Query(
        None,
        pattern="^(popular|latest-theatrical|upcoming-theatrical|recently-added|highest-rated|upcoming-ott|recent-ott|language)$",
    ),
    sort: str = "latest",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "start date must be on or before end date")
    context_query = _apply_filters(
        _movie_query(db),
        q=q,
        genre=genre,
        person=person,
        role=role,
        year=year,
        rating=rating,
        certification=certification,
        release_status=release_status,
        platform=platform,
        actor=actor,
        director=director,
        writer=writer,
        cinematographer=cinematographer,
        producer=producer,
        editor=editor,
        composer=composer,
        date_from=date_from,
        date_to=date_to,
    )
    context_query = _section_listing(context_query, section)
    language_counts = _language_counts(context_query)
    query = _apply_filters(
        context_query,
        language=language,
        language_region=language_region,
    )
    result = _page(query, sort, page, page_size)
    result["section"] = section
    result["filters"] = {
        "genres": [
            {"slug": x.slug, "name": x.name}
            for x in db.query(Genre).order_by(Genre.name)
        ],
        "languages": _public_languages(db),
        "language_groups": _public_language_groups(db),
        "language_counts": language_counts,
        "platforms": [item["name"] for item in _platform_catalog(db)],
        "roles": list(ROLE_ALIASES),
    }
    return result


@router.get("/languages")
def public_languages(db: Session = Depends(get_db)):
    """Return existing public movie languages with stable region metadata."""
    return _public_languages(db)


def _fold_search_term(raw_value: str) -> str:
    """Normalize a search term without touching non-ASCII scripts."""
    return " ".join(raw_value.strip().split())


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so user input is matched literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _movie_search_subquery(term: str, fuzzy: bool):
    """Build a UNION ALL subquery that returns one row per candidate movie:
    (movie_id, relevance_score, match_tier, match_source, token_count,
     matched_tokens, source_preference).

    match_source disambiguates the origin of the winning branch so the quality
    gate can prefer canonical matches over alternative-title accidents.

    token_count / matched_tokens track how many query tokens are present in the
    matched field — used for the multi-word quality gate.

    source_preference orders canonical sources ahead of alternative titles:
      title=0, original=1, alt=2.

    Tier order (first match wins, highest score):
      10000  exact title            (source: 'title')
       9500  exact original title    (source: 'original')
       9250  exact alternative title (source: 'alt')
       9000  title prefix            (source: 'title')
       8750  original title prefix   (source: 'original')
       8500  whole phrase            (source: 'title'/'original')
       8000  all query tokens in same field (source: 'title'/'original'/'alt')
       7000+ strong fuzzy (sim >= 0.60)     (source: 'title'/'original')
       5000+ trigram fuzzy fallback          (source: 'title'/'original'/'alt')

    Match-tier values:
      'exact'  - exact title/orig/alt match (score >= 9250)
      'prefix' - prefix match               (score >= 8750)
      'phrase' - full query phrase / substring of field (score = 8500)
      'all_tokens' - all query tokens present in field (score = 8000)
      'fuzzy'  - trigram fuzzy match         (score 5000+)
    """
    lower_term = term.lower()
    title_l = func.lower(Movie.title)
    orig_l = func.lower(func.coalesce(Movie.original_title, ""))
    prefix = _escape_like(lower_term) + "%"

    # Build query-token metadata for multi-word queries.
    query_tokens = [t for t in lower_term.split() if t]
    token_count = len(query_tokens)
    is_multiword = token_count >= 2

    # Each branch returns (movie_id, score, tier, source, token_count,
    # matched_tokens, source_preference)
    # token_count and matched_tokens are literal 0 for non-phrase branches;
    # they are only meaningful for phrase / all_tokens / fuzzy branches where
    # we can compute how many query tokens appear in the matched field.
    branches = [
        select(
            Movie.id.label('movie_id'),
            literal(10000).label('relevance_score'),
            literal('exact').label('match_tier'),
            literal('title').label('match_source'),
            literal(token_count).label('token_count'),
            literal(token_count).label('matched_tokens'),
            literal(0).label('source_preference'),
        ).where(title_l == lower_term),
        select(
            Movie.id.label('movie_id'),
            literal(9500).label('relevance_score'),
            literal('exact').label('match_tier'),
            literal('original').label('match_source'),
            literal(token_count).label('token_count'),
            literal(token_count).label('matched_tokens'),
            literal(1).label('source_preference'),
        ).where(orig_l == lower_term),
        select(
            Movie.id.label('movie_id'),
            literal(9250).label('relevance_score'),
            literal('exact').label('match_tier'),
            literal('alt').label('match_source'),
            literal(token_count).label('token_count'),
            literal(token_count).label('matched_tokens'),
            literal(2).label('source_preference'),
        )
        .join(AlternativeTitle, AlternativeTitle.movie_id == Movie.id)
        .where(func.lower(AlternativeTitle.title) == lower_term),
        select(
            Movie.id.label('movie_id'),
            literal(9000).label('relevance_score'),
            literal('prefix').label('match_tier'),
            literal('title').label('match_source'),
            literal(token_count).label('token_count'),
            literal(token_count).label('matched_tokens'),
            literal(0).label('source_preference'),
        ).where(title_l.like(prefix, escape="\\")),
        select(
            Movie.id.label('movie_id'),
            literal(8750).label('relevance_score'),
            literal('prefix').label('match_tier'),
            literal('original').label('match_source'),
            literal(token_count).label('token_count'),
            literal(token_count).label('matched_tokens'),
            literal(1).label('source_preference'),
        ).where(orig_l.like(prefix, escape="\\")),
    ]

    if is_multiword and token_count >= 2:
        # Phrase/contains: the full query appears as a substring of the field.
        # token_count/matched_tokens = token_count (full coverage).
        phrase_where = title_l.like(_escape_like(lower_term))
        branches.append(
            select(
                Movie.id.label('movie_id'),
                literal(8500).label('relevance_score'),
                literal('phrase').label('match_tier'),
                literal('title').label('match_source'),
                literal(token_count).label('token_count'),
                literal(token_count).label('matched_tokens'),
                literal(0).label('source_preference'),
            )
            .where(phrase_where)
            .where(title_l != lower_term)
            .where(~title_l.like(prefix, escape="\\"))
        )
        phrase_where_orig = orig_l.like(_escape_like(lower_term))
        branches.append(
            select(
                Movie.id.label('movie_id'),
                literal(8500).label('relevance_score'),
                literal('phrase').label('match_tier'),
                literal('original').label('match_source'),
                literal(token_count).label('token_count'),
                literal(token_count).label('matched_tokens'),
                literal(1).label('source_preference'),
            )
            .where(phrase_where_orig)
            .where(orig_l != lower_term)
            .where(~orig_l.like(prefix, escape="\\"))
        )
        # All-tokens-in-same-field: every query token appears in the field.
        # We build a compound LIKE: title_l LIKE %token1% AND title_l LIKE %token2%
        title_all_tokens_where = and_(
            *[title_l.like(f"%{_escape_like(t)}%") for t in query_tokens]
        )
        branches.append(
            select(
                Movie.id.label('movie_id'),
                literal(8000).label('relevance_score'),
                literal('all_tokens').label('match_tier'),
                literal('title').label('match_source'),
                literal(token_count).label('token_count'),
                literal(token_count).label('matched_tokens'),
                literal(0).label('source_preference'),
            )
            .where(title_all_tokens_where)
            .where(title_l != lower_term)
            .where(~title_l.like(prefix, escape="\\"))
            .where(~title_l.like(_escape_like(lower_term)))
        )
        orig_all_tokens_where = and_(
            *[orig_l.like(f"%{_escape_like(t)}%") for t in query_tokens]
        )
        branches.append(
            select(
                Movie.id.label('movie_id'),
                literal(8000).label('relevance_score'),
                literal('all_tokens').label('match_tier'),
                literal('original').label('match_source'),
                literal(token_count).label('token_count'),
                literal(token_count).label('matched_tokens'),
                literal(1).label('source_preference'),
            )
            .where(orig_all_tokens_where)
            .where(orig_l != lower_term)
            .where(~orig_l.like(prefix, escape="\\"))
            .where(~orig_l.like(_escape_like(lower_term)))
        )
        # All-tokens for alternative titles
        alt_all_tokens_where = and_(
            *[func.lower(AlternativeTitle.title).like(f"%{_escape_like(t)}%") for t in query_tokens]
        )
        branches.append(
            select(
                Movie.id.label('movie_id'),
                literal(8000).label('relevance_score'),
                literal('all_tokens').label('match_tier'),
                literal('alt').label('match_source'),
                literal(token_count).label('token_count'),
                literal(token_count).label('matched_tokens'),
                literal(2).label('source_preference'),
            )
            .join(AlternativeTitle, AlternativeTitle.movie_id == Movie.id)
            .where(alt_all_tokens_where)
            .where(func.lower(AlternativeTitle.title) != lower_term)
        )

    if fuzzy:
        title_sim = func.similarity(title_l, lower_term)
        orig_sim = func.similarity(orig_l, lower_term)
        # Piecewise scoring for canonical fuzzy (title/original):
        #   Strong fuzzy (sim >= SEARCH_STRONG_FUZZY_THRESHOLD) → 7000 + sim*999
        #     Range: 7599-7999, stays below all-token tier (8000).
        #   Normal fuzzy (sim >= SEARCH_FUZZY_THRESHOLD but < STRONG) → 5000 + sim*1999
        #     Range: 5799-6199.
        #   Alt fuzzy always uses: sim * 4990 (stays lowest, max 4990).
        canonical_fuzzy_score = case(
            (title_sim >= SEARCH_STRONG_FUZZY_THRESHOLD, 7000 + title_sim * 999),
            else_=5000 + title_sim * 1999,
        )
        canonical_fuzzy_score_orig = case(
            (orig_sim >= SEARCH_STRONG_FUZZY_THRESHOLD, 7000 + orig_sim * 999),
            else_=5000 + orig_sim * 1999,
        )
        # For multi-word token coverage on fuzzy, compute how many tokens appear.
        if is_multiword:
            title_cases = [case((title_l.like(f"%{_escape_like(t)}%"), 1), else_=0) for t in query_tokens]
            orig_cases = [case((orig_l.like(f"%{_escape_like(t)}%"), 1), else_=0) for t in query_tokens]
            alt_cases = [case((func.lower(AlternativeTitle.title).like(f"%{_escape_like(t)}%"), 1), else_=0) for t in query_tokens]
            title_token_count_expr = title_cases[0] + sum(title_cases[1:])
            orig_token_count_expr = orig_cases[0] + sum(orig_cases[1:])
            alt_token_count_expr = alt_cases[0] + sum(alt_cases[1:])
        else:
            title_token_count_expr = literal(1)
            orig_token_count_expr = literal(1)
            alt_token_count_expr = literal(1)

        # For multi-word queries, the canonical fuzzy branch must satisfy a
        # token-coverage gate to avoid partial single-token matches.
        # E.g. query "another woman" should not match title "Another" (sim 0.57
        # via trigram but 0 of 2 tokens covered) or "Woman" (sim 0.43, 0/2).
        # Allowed when: sim >= SEARCH_STRONG_FUZZY_THRESHOLD (whole-phrase fuzzy
        # recovery) OR matched_tokens == token_count (all tokens present).
        title_fuzzy_where_extra = literal(True)
        orig_fuzzy_where_extra = literal(True)
        if is_multiword:
            title_fuzzy_where_extra = or_(
                title_sim >= SEARCH_STRONG_FUZZY_THRESHOLD,
                title_token_count_expr >= token_count,
            )
            orig_fuzzy_where_extra = or_(
                orig_sim >= SEARCH_STRONG_FUZZY_THRESHOLD,
                orig_token_count_expr >= token_count,
            )

        branches.append(
            select(
                Movie.id.label('movie_id'),
                cast(canonical_fuzzy_score, Integer).label('relevance_score'),
                literal('fuzzy').label('match_tier'),
                literal('title').label('match_source'),
                literal(token_count).label('token_count'),
                title_token_count_expr.label('matched_tokens'),
                literal(0).label('source_preference'),
            )
            .where(title_sim >= SEARCH_FUZZY_THRESHOLD)
            .where(title_l != lower_term)
            .where(~title_l.like(prefix, escape="\\"))
            .where(title_fuzzy_where_extra)
        )
        branches.append(
            select(
                Movie.id.label('movie_id'),
                cast(canonical_fuzzy_score_orig, Integer).label('relevance_score'),
                literal('fuzzy').label('match_tier'),
                literal('original').label('match_source'),
                literal(token_count).label('token_count'),
                orig_token_count_expr.label('matched_tokens'),
                literal(1).label('source_preference'),
            )
            .where(orig_sim >= SEARCH_FUZZY_THRESHOLD)
            .where(orig_l != lower_term)
            .where(~orig_l.like(prefix, escape="\\"))
            .where(orig_fuzzy_where_extra)
        )
        branches.append(
            select(
                Movie.id.label('movie_id'),
                cast(func.max(func.similarity(func.lower(AlternativeTitle.title), lower_term)) * 4990, Integer).label('relevance_score'),
                literal('fuzzy').label('match_tier'),
                literal('alt').label('match_source'),
                literal(token_count).label('token_count'),
                func.max(alt_token_count_expr).label('matched_tokens'),
                 literal(2).label('source_preference'),
             )
             .join(AlternativeTitle, AlternativeTitle.movie_id == Movie.id)
             .where(func.lower(AlternativeTitle.title).op("%")(lower_term))
             .having(func.max(func.similarity(func.lower(AlternativeTitle.title), lower_term)) >= SEARCH_STRONG_FUZZY_THRESHOLD)
              .group_by(Movie.id)
          )

    union_sq = union_all(*branches).subquery()

    # Deduplicate so each movie keeps the match_tier and match_source that
    # belong to its *winning* score, not an aggregate of unrelated fields.
    # We use ROW_NUMBER() partitioned by movie_id, ordered by score DESC then
    # source_preference ASC (canonical preferred), then keep only rn=1.
    row_num = (
        func.row_number()
        .over(
            partition_by=union_sq.c.movie_id,
            order_by=[
                union_sq.c.relevance_score.desc(),
                union_sq.c.source_preference.asc(),
                union_sq.c.match_tier.desc(),
            ],
        )
        .label("rn")
    )
    ranked = (
        select(
            union_sq.c.movie_id,
            union_sq.c.relevance_score,
            union_sq.c.match_tier,
            union_sq.c.match_source,
            union_sq.c.token_count,
            union_sq.c.matched_tokens,
            union_sq.c.source_preference,
            row_num,
        )
        .subquery()
    )
    dedup = (
        select(
            ranked.c.movie_id,
            ranked.c.relevance_score,
            ranked.c.match_tier,
            ranked.c.match_source,
            ranked.c.token_count,
            ranked.c.matched_tokens,
            ranked.c.source_preference,
        )
        .where(ranked.c.rn == 1)
        .subquery()
    )
    return dedup


def _person_search_subquery(term: str, fuzzy: bool):
    """Build a UNION ALL subquery returning (person_id, relevance_score)."""
    lower_term = term.lower()
    name_l = func.lower(Person.name)
    prefix = _escape_like(lower_term) + "%"

    branches = [
        select(Person.id.label('person_id'), literal(10000).label('relevance_score')).where(name_l == lower_term),
        select(Person.id.label('person_id'), literal(8000).label('relevance_score')).where(name_l.like(prefix, escape="\\")),
    ]
    if fuzzy:
        name_sim = func.similarity(name_l, lower_term)
        branches.append(
            select(
                Person.id.label('person_id'),
                cast(name_sim * 4990, Integer).label('relevance_score'),
            )
            .where(name_sim >= SEARCH_FUZZY_THRESHOLD)
        )

    union_sq = union_all(*branches).subquery()
    dedup = (
        select(
            union_sq.c.person_id,
            func.max(union_sq.c.relevance_score).label("relevance_score"),
        )
        .group_by(union_sq.c.person_id)
        .subquery()
    )
    return dedup


@router.get("/search")
def global_search(
    request: Request,
    q: str = Query("", max_length=200),
    language: str | None = None,
    language_region: str = Query("all", pattern="^(indian|international|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    empty = {
        "movies": {"items": [], "total": 0},
        "people": {"items": [], "total": 0},
        "page": page,
        "page_size": page_size,
        "filters": {
            "languages": _public_languages(db),
            "language_groups": _public_language_groups(db),
            "language_counts": _empty_language_counts(),
        },
    }
    term = _fold_search_term(q)
    if not term:
        return empty
    limit(request, "global-search", 120, 60)
    fuzzy = len(term) >= SEARCH_MIN_TRIGRAM_LENGTH

    movie_sq = _movie_search_subquery(term, fuzzy)
    base_movie_query = _movie_query(db).join(
        movie_sq, movie_sq.c.movie_id == Movie.id
    )
    query_tokens = [t for t in term.lower().split() if t]
    is_multiword = len(query_tokens) >= 2
    if fuzzy:
        # ------------------------------------------------------------------
        # Quality gate — multi-tier:
        #
        # Tier A  Strong non-fuzzy match (canonical exact/prefix/phrase/
        #         all_tokens): suppresses fuzzy-only subset matches
        #
        # Tier B  Strong canonical fuzzy match (sim >= STRONG threshold):
        #         suppresses weak alt-title-only exact matches when a much
        #         stronger canonical fuzzy match exists.
        #         (e.g. "avesham" → Aavesham fuzzy > Vedalam alt exact)
        #
        # Tier C  Fuzzy score-gap gate: when the best fuzzy match is strong
        #         (>= STRONG threshold), suppress fuzzy candidates whose
        #         similarity is more than SEARCH_FUZZY_MAX_GAP below the best.
        # ------------------------------------------------------------------

        # Tier A: Strong non-fuzzy canonical matches exist.
        # If so, suppress *all* fuzzy candidates that match only a subset of
        # query tokens (multi-word case) — these are partial matches like
        # "Another" or "Woman" when "Another Woman" exists.
        strong_tiers = ['exact', 'prefix', 'phrase', 'all_tokens']
        canonical_strong_count = (
            db.query(func.count())
            .select_from(movie_sq)
            .filter(
                movie_sq.c.match_tier.in_(strong_tiers),
                movie_sq.c.match_source.in_(['title', 'original']),
            )
            .scalar()
        )
        if canonical_strong_count > 0:
            if is_multiword:
                # Suppress fuzzy candidates that don't cover ALL query tokens.
                # A fuzzy match must have matched_tokens == token_count OR
                # similarity high enough to be a full-phrase typo.
                base_movie_query = base_movie_query.filter(
                    or_(
                        movie_sq.c.match_tier != 'fuzzy',
                        movie_sq.c.matched_tokens >= movie_sq.c.token_count,
                    )
                )
            else:
                base_movie_query = base_movie_query.filter(
                    movie_sq.c.match_tier != 'fuzzy'
                )

        # Tier B: Canonical intent override.
        # When a strong canonical fuzzy match exists (sim >= STRONG threshold,
        # score >= 7600) AND there is no canonical exact/prefix/phrase match,
        # the query clearly intends the movie whose canonical title closely
        # resembles the query.  Suppress alt-source matches from *different*
        # movies — they are alias noise (e.g. "avesham" → Aavesham fuzzy 7700
        # vs Vedalam alt-exact 9250).  Alt-title matches for the *same*
        # canonical movie are preserved (legitimate alias).
        #
        # This tier does NOT fire when a canonical non-fuzzy match exists
        # (e.g. "vaazha" → Vaazha exact 10000 + Alternate Veethi alt-exact 9250)
        # because in that case the alt match is intentional.
        best_canonical_fuzzy = (
            db.query(func.max(movie_sq.c.relevance_score))
            .select_from(movie_sq)
            .filter(
                movie_sq.c.match_tier == 'fuzzy',
                movie_sq.c.match_source.in_(['title', 'original']),
            )
            .scalar()
        )
        if canonical_strong_count == 0 and best_canonical_fuzzy and best_canonical_fuzzy >= 7599:
            # Strong canonical fuzzy match exists (but no canonical non-fuzzy).
            # Get the movie_id(s) that produced the best canonical fuzzy match.
            canonical_fuzzy_movie_ids = (
                db.query(movie_sq.c.movie_id)
                .select_from(movie_sq)
                .filter(
                    movie_sq.c.match_tier == 'fuzzy',
                    movie_sq.c.match_source.in_(['title', 'original']),
                    movie_sq.c.relevance_score >= best_canonical_fuzzy - 149,
                )
                .distinct()
                .all()
            )
            canonical_movie_ids = {row.movie_id for row in canonical_fuzzy_movie_ids}
            # Suppress all alt-source matches for movies that did NOT
            # produce the canonical fuzzy winner (exact, prefix, fuzzy alt alike).
            base_movie_query = base_movie_query.filter(
                or_(
                    movie_sq.c.match_source != 'alt',
                    movie_sq.c.movie_id.in_(canonical_movie_ids),
                )
            )

        # Tier C: Fuzzy score-gap gate. If the best fuzzy match is strong,
        # suppress fuzzy candidates that are substantially worse.
        best_fuzzy_score = (
            db.query(func.max(movie_sq.c.relevance_score))
            .select_from(movie_sq)
            .filter(movie_sq.c.match_tier == 'fuzzy')
            .scalar()
        )
        if canonical_strong_count > 0 and best_fuzzy_score and best_fuzzy_score >= 7599:
            # Best fuzzy is strong (>=7599 corresponds to sim >= 0.60).
            # Suppress fuzzy candidates below best - gap_threshold_score.
            # Strong fuzzy: score = 7000 + sim * 999, so 0.15 sim gap ≈ 150 score.
            # Since Tier C only fires when best is strong fuzzy (>=0.60),
            # use strong fuzzy's multiplier (999) for gap conversion.
            gap_score = int(SEARCH_FUZZY_MAX_GAP * 999)
            base_movie_query = base_movie_query.filter(
                or_(
                    movie_sq.c.match_tier != 'fuzzy',
                    movie_sq.c.relevance_score >= best_fuzzy_score - gap_score,
                )
            )
    language_counts = _language_counts(base_movie_query)
    movie_context_query = _apply_filters(
        base_movie_query,
        language=language,
        language_region=language_region,
    )
    movie_query = movie_context_query.with_entities(Movie)
    movie_total = movie_query.count()
    movies = (
        movie_context_query.order_by(
            movie_sq.c.relevance_score.desc(),
            Movie.popularity.desc(),
            Movie.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    person_sq = _person_search_subquery(term, fuzzy)
    people_total = db.query(func.count()).select_from(person_sq).scalar()
    people = (
        db.query(Person)
        .join(person_sq, person_sq.c.person_id == Person.id)
        .order_by(person_sq.c.relevance_score.desc(), Person.name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "movies": {"items": cards(movies), "total": movie_total},
        "people": {
            "items": [
                {
                    "id": x.id,
                    "name": x.name,
                    "profile_path": x.profile_path,
                    "department": x.known_for_department,
                }
                for x in people
            ],
            "total": people_total,
        },
        "page": page,
        "page_size": page_size,
        "filters": {
            "languages": _public_languages(db),
            "language_groups": _public_language_groups(db),
            "language_counts": language_counts,
        },
    }


@router.get("/ott")
def ott_landing(language: str | None = None, db: Session = Depends(get_db)):
    today = site_date()
    valid = (
        OttAvailability.status.in_(PUBLIC_OTT_STATES),
        func.upper(OttAvailability.country) == "IN",
    )
    movie_context_query = _movie_query(db).join(OttAvailability).filter(*valid)
    language_counts = _language_counts(movie_context_query)
    movie_query = _apply_language_filter(movie_context_query, language)
    confirmed_query = movie_query.filter(
        OttAvailability.verification_status == "CONFIRMED"
    )
    return {
        "platforms": _platform_catalog(db, public_only=True),
        "upcoming": cards(
            confirmed_query.filter(OttAvailability.ott_release_date > today)
            .order_by(OttAvailability.ott_release_date)
            .limit(24)
            .all()
        ),
        "recent": cards(
            confirmed_query.filter(OttAvailability.ott_release_date <= today)
            .order_by(OttAvailability.ott_release_date.desc())
            .limit(24)
            .all()
        ),
        "confirmed": cards(
            confirmed_query.order_by(OttAvailability.updated_at.desc()).limit(24).all()
        ),
        "language": normalize_language_code(language) or "all",
        "languages": _public_languages(db),
        "language_counts": language_counts,
    }


@router.get("/ott/{platform}")
def ott_platform(
    platform: str,
    sort: str = "ott-release",
    language: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    db: Session = Depends(get_db),
):
    canonical, aliases = _provider_aliases(db, platform)
    context_query = _movie_query(db).filter(
        Movie.ott_availabilities.any(
            and_(
                OttAvailability.provider.in_(aliases),
                func.upper(OttAvailability.country) == "IN",
            )
        )
    )
    language_counts = _language_counts(context_query)
    query = _apply_language_filter(context_query, language)
    result = _page(query, sort, page, page_size)
    result.update(
        {
            "platform": canonical,
            "upcoming": [],
            "recent": [],
            "available": result["items"],
            "language": normalize_language_code(language) or "all",
            "languages": _public_languages(db),
            "language_counts": language_counts,
        }
    )
    today = site_date()
    result["upcoming"] = cards(
        query.join(OttAvailability)
        .filter(
            OttAvailability.provider.in_(aliases),
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.verification_status == "CONFIRMED",
            OttAvailability.ott_release_date > today,
        )
        .order_by(OttAvailability.ott_release_date)
        .limit(12)
        .all()
    )
    result["recent"] = cards(
        query.join(OttAvailability)
        .filter(
            OttAvailability.provider.in_(aliases),
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.verification_status == "CONFIRMED",
            OttAvailability.ott_release_date <= today,
        )
        .order_by(OttAvailability.ott_release_date.desc())
        .limit(12)
        .all()
    )
    return result


@router.get("/people/{person_id}")
def person_detail(
    person_id: int,
    sort: str = "newest",
    credit_type: str = "all",
    role: str | None = None,
    db: Session = Depends(get_db),
):
    from app.seo import person_is_indexable

    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    query = (
        db.query(MovieCredit)
        .filter(MovieCredit.person_id == person_id)
        .join(Movie)
        .filter(Movie.safety_public_visibility == "FULL")
        .options(
            selectinload(MovieCredit.movie).selectinload(Movie.genres),
            selectinload(MovieCredit.movie).selectinload(Movie.languages),
            selectinload(MovieCredit.movie).selectinload(Movie.ratings),
            selectinload(MovieCredit.movie).selectinload(Movie.external_ids),
        )
    )
    if credit_type in {"cast", "crew"}:
        query = query.filter(MovieCredit.credit_type == credit_type)
    normalized = normalize_role(role)
    if normalized:
        aliases = ROLE_ALIASES.get(normalized, (normalized,))
        query = query.filter(
            or_(
                func.lower(MovieCredit.job).in_(aliases),
                func.lower(MovieCredit.department).in_(aliases),
                and_(normalized == "actor", MovieCredit.credit_type == "cast"),
            )
        )
    credits = query.order_by(
        _primary_release_date_query().asc()
        if sort == "oldest"
        else _primary_release_date_query().desc()
    ).all()
    return {
        "id": person.id,
        "name": person.name,
        "display_id": person.tmdb_id,
        "profile_path": person.profile_path,
        "department": person.known_for_department,
        "seo_indexable": person_is_indexable(db, person_id),
        "biography": person.biography,
        "birthday": person.birthday,
        "place_of_birth": person.place_of_birth,
        "imdb_id": person.imdb_id,
        "imdb_url": (
            f"https://www.imdb.com/name/{person.imdb_id}/" if person.imdb_id else None
        ),
        "roles": sorted(
            {
                normalize_role(x.job or x.department or x.credit_type)
                for x in credits
                if normalize_role(x.job or x.department or x.credit_type)
            }
        ),
        "filmography": [
            {
                "movie": _card(x.movie),
                "character": x.character,
                "job": x.job,
                "department": x.department,
                "credit_type": x.credit_type,
                "normalized_role": normalize_role(
                    x.job or x.department or x.credit_type
                ),
            }
            for x in credits
        ],
    }


@router.get("/calendar/{period}")
def calendar(
    period: str,
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    language: str | None = None,
    db: Session = Depends(get_db),
):
    today = site_date()
    monday = today - timedelta(days=today.weekday())
    this_month = today.replace(day=1)
    previous_month = (this_month - timedelta(days=1)).replace(day=1)
    next_month = this_month.replace(
        day=monthrange(this_month.year, this_month.month)[1]
    ) + timedelta(days=1)
    after_next = next_month.replace(
        day=monthrange(next_month.year, next_month.month)[1]
    ) + timedelta(days=1)
    ranges = {
        "previous-week": (monday - timedelta(days=7), monday),
        "this-week": (monday, monday + timedelta(days=7)),
        "next-week": (monday + timedelta(days=7), monday + timedelta(days=14)),
        "previous-month": (previous_month, this_month),
        "this-month": (this_month, next_month),
        "next-month": (next_month, after_next),
    }
    if period not in ranges:
        raise HTTPException(404, "Unknown calendar period")
    if month:
        start = datetime.strptime(f"{month}-01", "%Y-%m-%d").date()
        end = start.replace(day=monthrange(start.year, start.month)[1]) + timedelta(
            days=1
        )
    else:
        start, end = ranges[period]
    normalized_language = normalize_language_code(language) or "all"
    release_context_query = (
        db.query(MovieReleaseDate)
        .join(Movie)
        .options(
            selectinload(MovieReleaseDate.movie).selectinload(Movie.genres),
            selectinload(MovieReleaseDate.movie).selectinload(Movie.languages),
            selectinload(MovieReleaseDate.movie).selectinload(Movie.ratings),
            selectinload(MovieReleaseDate.movie).selectinload(Movie.external_ids),
            selectinload(MovieReleaseDate.movie).selectinload(Movie.release_dates),
            selectinload(MovieReleaseDate.movie).selectinload(Movie.ott_availabilities),
        )
        .filter(
            Movie.safety_public_visibility == "FULL",
            Movie.adult.is_(False),
            MovieReleaseDate.release_date >= start,
            MovieReleaseDate.release_date < end,
            func.lower(MovieReleaseDate.release_type).in_(THEATRICAL_RELEASE_TYPES),
        )
    )
    theatrical_language_counts = _language_counts(release_context_query)
    release_query = _apply_language_filter(release_context_query, language)
    release_rows = release_query.order_by(
        MovieReleaseDate.release_date, MovieReleaseDate.country.desc()
    ).all()
    candidate_movies = {}
    for item in release_rows:
        candidate_movies[item.movie_id] = item.movie
    theatrical = []
    for movie in candidate_movies.values():
        classification = classify_release(movie, today=today)
        selected = classification.theatrical_date
        if selected is None or not start <= selected < end:
            continue
        matching = next(
            (
                item
                for item in movie.release_dates
                if item.release_date == selected
                and (item.release_type or "").strip().lower()
                in THEATRICAL_RELEASE_TYPES
            ),
            None,
        )
        theatrical.append(
            _card(movie)
            | {
                "release_date": selected,
                "theatrical_release_date": selected,
                "certification": matching.certification if matching else None,
            }
        )
    theatrical.sort(key=lambda value: (value["release_date"], value["id"]))
    ott_context_query = (
        db.query(Movie, OttAvailability)
        .options(
            selectinload(Movie.genres),
            selectinload(Movie.languages),
            selectinload(Movie.ratings),
            selectinload(Movie.external_ids),
        )
        .join(OttAvailability)
        .filter(
            Movie.safety_public_visibility == "FULL",
            Movie.adult.is_(False),
            OttAvailability.ott_release_date >= start,
            OttAvailability.ott_release_date < end,
            # A stored OTT date is the calendar source of truth. Its workflow,
            # verification, source, confidence, and enrichment state are not
            # calendar eligibility criteria.
        )
    )
    ott_language_counts = _language_counts(ott_context_query)
    ott_query = _apply_language_filter(ott_context_query, language)
    availability_rows = (
        ott_query.order_by(
            OttAvailability.ott_release_date, Movie.title, OttAvailability.provider
        )
        .all()
    )
    evidence_query = _apply_language_filter(
        db.query(Movie, OttEvidence)
        .join(OttEvidence)
        .options(
            selectinload(Movie.genres),
            selectinload(Movie.languages),
            selectinload(Movie.ratings),
            selectinload(Movie.external_ids),
        )
        .filter(
            Movie.safety_public_visibility == "FULL",
            Movie.adult.is_(False),
            OttEvidence.release_date >= start,
            OttEvidence.release_date < end,
            OttEvidence.rejected_at.is_(None),
        ),
        language,
    )
    source_query = _apply_language_filter(
        db.query(Movie, OttSourceRelease)
        .join(OttSourceRelease, OttSourceRelease.matched_movie_id == Movie.id)
        .options(
            selectinload(Movie.genres),
            selectinload(Movie.languages),
            selectinload(Movie.ratings),
            selectinload(Movie.external_ids),
        )
        .filter(
            Movie.safety_public_visibility == "FULL",
            Movie.adult.is_(False),
            OttSourceRelease.release_date >= start,
            OttSourceRelease.release_date < end,
        ),
        language,
    )
    release_date_query = _apply_language_filter(
        db.query(Movie, MovieReleaseDate)
        .join(MovieReleaseDate)
        .options(
            selectinload(Movie.genres),
            selectinload(Movie.languages),
            selectinload(Movie.ratings),
            selectinload(Movie.external_ids),
        )
        .filter(
            Movie.safety_public_visibility == "FULL",
            Movie.adult.is_(False),
            MovieReleaseDate.release_date >= start,
            MovieReleaseDate.release_date < end,
            func.lower(MovieReleaseDate.release_type).in_(("4", "digital", "ott", "streaming")),
        ),
        language,
    )
    ott_rows = [
        (movie, row.provider, row.ott_release_date, row.country, row.provider_logo)
        for movie, row in availability_rows
    ]
    ott_rows.extend(
        (movie, row.platform, row.release_date, row.country, None)
        for movie, row in evidence_query.all()
    )
    ott_rows.extend(
        (movie, row.platform, row.release_date, None, None)
        for movie, row in source_query.all()
    )
    ott_rows.extend(
        (movie, None, row.release_date, row.country, None)
        for movie, row in release_date_query.all()
    )
    # One card per local movie and OTT release date. Multiple confirmed
    # providers on the same date are combined without moving later releases
    # into an earlier date group.
    grouped = {}
    for movie, provider, release_date, country, provider_logo in ott_rows:
        key = (movie.id, release_date)
        item = grouped.setdefault(key, _card(movie) | {
            "release_date": release_date,
            "ott_release_date": release_date,
            "ott_platform_slug": platform_slug(provider) if provider else None,
            "ott_platform_logo": provider_logo,
            "ott_releases": [],
        })
        release = {"platform": normalize_platform(provider) if provider else "Platform TBA",
                   "release_date": release_date, "country": country}
        existing_release = next(
            (
                value
                for value in item["ott_releases"]
                if value["platform"] == release["platform"]
                and value["release_date"] == release["release_date"]
            ),
            None,
        )
        if existing_release is None:
            item["ott_releases"].append(release)
        elif not existing_release["country"] and country:
            existing_release["country"] = country
    ott = list(grouped.values())
    for item in ott:
        item["ott_platforms"] = sorted({x["platform"] for x in item["ott_releases"]})
        item["ott_platform"] = ", ".join(item["ott_platforms"])
    ott.sort(
        key=lambda value: (
            value["ott_release_date"],
            value["title"].casefold(),
            value["id"],
        )
    )
    return {
        "period": period,
        "today": today,
        "start_date": start,
        "end_date": end - timedelta(days=1),
        "language": normalized_language,
        "languages": _public_languages(db),
        "language_counts": {
            "theatrical": theatrical_language_counts,
            "ott": ott_language_counts,
        },
        "theatrical": {"items": theatrical, "total": len(theatrical)},
        "ott": {"items": ott, "total": len(ott)},
    }


@router.get("/movies/{movie_id}/detail")
def movie_detail(movie_id: int, db: Session = Depends(get_db)):
    movie = (
        db.query(Movie)
        .options(
            selectinload(Movie.genres),
            selectinload(Movie.languages),
            selectinload(Movie.ratings),
            selectinload(Movie.external_ids),
            selectinload(Movie.ott_availabilities),
            selectinload(Movie.trailers),
        )
        .filter(Movie.id == movie_id)
        .filter(Movie.safety_public_visibility != "HIDDEN")
        .first()
    )
    if not movie:
        raise HTTPException(404, "Movie not found")
    credits = (
        db.query(MovieCredit)
        .filter_by(movie_id=movie_id)
        .options(selectinload(MovieCredit.person))
        .order_by(
            MovieCredit.credit_type,
            MovieCredit.cast_order,
            MovieCredit.department,
            MovieCredit.job,
        )
        .all()
    )
    images = (
        db.query(MovieImage)
        .filter_by(movie_id=movie_id)
        .order_by(MovieImage.image_type, MovieImage.is_primary.desc())
        .all()
    )
    releases = (
        db.query(MovieReleaseDate)
        .filter_by(movie_id=movie_id)
        .order_by(MovieReleaseDate.release_date)
        .all()
    )
    companies = (
        db.query(ProductionCompany)
        .join(
            MovieProductionCompany,
            MovieProductionCompany.production_company_id == ProductionCompany.id,
        )
        .filter(MovieProductionCompany.movie_id == movie_id)
        .all()
    )
    countries = (
        db.query(ProductionCountry)
        .join(
            MovieProductionCountry,
            MovieProductionCountry.production_country_id == ProductionCountry.id,
        )
        .filter(MovieProductionCountry.movie_id == movie_id)
        .all()
    )
    keywords = (
        db.query(Keyword)
        .join(MovieKeyword, MovieKeyword.keyword_id == Keyword.id)
        .filter(MovieKeyword.movie_id == movie_id)
        .order_by(Keyword.name)
        .all()
    )
    certification = next(
        (x.certification for x in releases if x.country == "IN" and x.certification),
        None,
    ) or next((x.certification for x in releases if x.certification), None)
    classification, _, _ = ReleaseStatusService(
        db
    ).classify_movie(movie, sync_evidence=False)
    ott_summary = best_canonical_ott(movie)
    confirmed_ott = confirmed_canonical_ott(movie)
    platforms = sorted({normalize_platform(x.provider) for x in movie.ott_availabilities
                          if x.status in PUBLIC_OTT_STATES and normalize_platform(x.provider)})
    grouped_crew = {}
    for credit in credits:
        normalized = normalize_role(credit.job or credit.department)
        if credit.credit_type == "crew" and normalized:
            grouped_crew.setdefault(normalized, []).append(
                {
                    "person_id": credit.person_id,
                    "name": credit.person.name,
                    "profile_path": credit.person.profile_path,
                    "job": credit.job,
                    "department": credit.department,
                }
            )
    grouped_releases = {"theatrical": [], "digital": [], "streaming": [], "other": []}
    for item in releases:
        release_type = item.release_type.lower()
        group = (
            "theatrical"
            if release_type in {"2", "3", "limited theatrical", "theatrical"}
            else (
                "digital"
                if release_type in {"4", "digital"}
                else "streaming" if release_type in {"ott", "streaming"} else "other"
            )
        )
        grouped_releases[group].append(
            {
                "country": item.country,
                "date": item.release_date,
                "type": item.release_type,
                "certification": item.certification,
                "note": item.note,
            }
        )
    payload = {
        "movie": _card(movie)
        | {
            "display_id": movie.tmdb_id,
            "tagline": movie.tagline,
            "runtime_minutes": movie.runtime_minutes,
            "status": movie.status,
            "release_status": classification.label,
            "release_status_code": classification.code,
            "theatrical_release_date": classification.theatrical_date,
            "ott_platform": (
                normalize_platform(ott_summary.provider) if ott_summary else None
            ),
            "ott_release_date": (
                confirmed_ott.ott_release_date if confirmed_ott else None
            ),
            "certification": certification,
            "budget": movie.budget,
            "revenue": movie.revenue,
            "collection": (
                {
                    "name": movie.collection_name,
                    "poster_path": movie.collection_poster_path,
                    "backdrop_path": movie.collection_backdrop_path,
                }
                if movie.collection_name
                else None
            ),
            "original_language": movie.original_language,
            "original_language_name": language_name(
                movie.original_language,
                next(
                    (
                        x.english_name
                        for x in movie.languages
                        if x.iso_639_1 == movie.original_language
                    ),
                    None,
                ),
            ),
            "spoken_languages": [
                {
                    "code": x.iso_639_1,
                    "name": language_name(x.iso_639_1, x.english_name),
                }
                for x in movie.languages
            ],
            "production_countries": [
                {"code": x.iso_3166_1, "name": x.name} for x in countries
            ],
            "production_companies": [
                {"name": x.name, "logo": x.logo_path, "country": x.origin_country}
                for x in companies
            ],
            "ott": [
                {
                    "provider": normalize_platform(x.provider),
                    "logo": x.provider_logo,
                    "watch_type": x.watch_type,
                    "release_date": (
                        x.ott_release_date
                        if x.verification_status == "CONFIRMED"
                        else None
                    ),
                    "country": x.country,
                    "source_url": x.source_url,
                    "original_premiere": x.is_original_premiere,
                    "attribution": (
                        "Watch-provider data supplied by TMDB and sourced through JustWatch"
                        if (x.source_type or "").lower() in {"tmdb", "justwatch_tmdb"}
                        else None
                    ),
                }
                for x in _canonical_ott_rows(movie)
            ],
        },
        "cast": _cast_payload(credits),
        "crew": [
            {
                "person_id": x.person_id,
                "name": x.person.name,
                "profile_path": x.person.profile_path,
                "job": x.job,
                "department": x.department,
                "normalized_role": normalize_role(x.job or x.department),
            }
            for x in credits
            if x.credit_type == "crew"
        ],
        "crew_by_role": grouped_crew,
        "images": [
            {
                "type": x.image_type,
                "url": x.local_path or x.original_url,
                "language": x.language,
                "width": x.width,
                "height": x.height,
                "aspect_ratio": x.aspect_ratio,
                "primary": x.is_primary,
            }
            for x in images
        ],
        "releases": [
            {
                "country": x.country,
                "date": x.release_date,
                "type": x.release_type,
                "certification": x.certification,
                "note": x.note,
            }
            for x in releases
        ],
        "release_groups": grouped_releases,
        "ratings": _ratings_payload(movie),
        "community_rating": _community_rating_summary(db, movie_id),
        "keywords": [x.name for x in keywords],
        "alternative_titles": [
            {"title": x.title, "country": x.country, "type": x.title_type}
            for x in db.query(AlternativeTitle)
            .filter_by(movie_id=movie_id)
            .order_by(AlternativeTitle.country, AlternativeTitle.title)
            .all()
        ],
        "external_ids": [
            _external_id_payload(x)
            for x in db.query(ExternalId).filter_by(movie_id=movie_id).all()
            if not _is_metadata_provider(x.provider)
        ],
        "trailer": trailer_payload(
            next((item for item in movie.trailers if item.is_primary), None)
            or TrailerService(db).primary(movie.id)
        ),
        "videos": trailer_videos_payload(movie),
    }
    primary_trailer = next((item for item in movie.trailers if item.is_primary and not item.is_unavailable), None)
    quality = evaluate_movie_ads_quality(movie, credits=credits, releases=releases, trailer=primary_trailer)
    timeline = []
    labels = {"2": "Limited theatrical release", "3": "Theatrical release", "4": "Digital release", "theatrical": "Theatrical release", "digital": "Digital release", "ott": "OTT release", "streaming": "OTT release"}
    for row in releases:
        timeline.append({"date": row.release_date, "label": labels.get(row.release_type.lower(), f"{row.release_type} release"), "kind": "release"})
    for row in _canonical_ott_rows(movie):
        if row.ott_release_date:
            timeline.append({"date": row.ott_release_date, "label": "OTT release", "kind": "ott"})
    timeline.sort(key=lambda item: str(item["date"]))
    payload["release_timeline"] = timeline
    payload["ads"] = {"eligible": quality.eligible, "reason": quality.reason}
    # Public GET endpoints are deliberately read-only. Maintenance and admin
    # workflows own all repair scheduling.
    payload["repair_queued"] = False
    return payload


@router.get("/movies/{movie_id}/community-rating")
def community_rating(movie_id: int, db: Session = Depends(get_db)):
    if not db.query(Movie.id).filter(
        Movie.id == movie_id,
        Movie.safety_public_visibility != "HIDDEN",
    ).first():
        raise HTTPException(404, "Movie not found")
    return _community_rating_summary(db, movie_id)


@router.get("/movies/{movie_id}/community-rating/mine")
def my_community_rating(
    movie_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not db.query(Movie.id).filter(
        Movie.id == movie_id,
        Movie.safety_public_visibility != "HIDDEN",
    ).first():
        raise HTTPException(404, "Movie not found")
    rating = db.query(CommunityRating).filter_by(
        user_id=user.id, movie_id=movie_id
    ).first()
    return {"rating": float(rating.rating) if rating else None}


@router.put(
    "/movies/{movie_id}/community-rating",
    dependencies=[Depends(require_user_same_origin)],
)
def rate_movie(
    movie_id: int,
    payload: CommunityRatingSubmission,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not db.query(Movie.id).filter(
        Movie.id == movie_id,
        Movie.safety_public_visibility != "HIDDEN",
    ).first():
        raise HTTPException(404, "Movie not found")
    rating = db.query(CommunityRating).filter_by(
        user_id=user.id, movie_id=movie_id
    ).first()
    created = rating is None
    if rating is None:
        rating = CommunityRating(
            user_id=user.id, movie_id=movie_id, rating=payload.rating
        )
        db.add(rating)
    else:
        rating.rating = payload.rating
    db.commit()
    return {
        **_community_rating_summary(db, movie_id),
        "user_rating": float(rating.rating),
        "created": created,
    }


@router.get("/movies/{movie_id}/public-comments")
@router.get("/movies/{movie_id}/comments")
def movie_comments(
    movie_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    if not db.query(Movie.id).filter(
        Movie.id == movie_id,
        Movie.safety_public_visibility != "HIDDEN",
    ).first():
        raise HTTPException(404, "Movie not found")
    query = db.query(MovieComment).filter_by(movie_id=movie_id, status="APPROVED")
    total = query.count()
    rows = (
        query.order_by(MovieComment.created_at.desc(), MovieComment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [
            {
                "id": item.id,
                "display_name": item.display_name,
                "comment": item.comment_text,
                "created_at": item.created_at,
            }
            for item in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.post("/movies/{movie_id}/comments", status_code=201)
def submit_movie_comment(
    movie_id: int,
    payload: CommentSubmission,
    request: Request,
    db: Session = Depends(get_db),
):
    if not db.query(Movie.id).filter(
        Movie.id == movie_id,
        Movie.safety_public_visibility != "HIDDEN",
    ).first():
        raise HTTPException(404, "Movie not found")
    client_ip = request.client.host if request.client else "unknown"
    limit(request, "movie-comment", 5, 3600, identity=f"{client_ip}:{movie_id}")
    ip_hash = hashlib.sha256(f"{settings.SECRET_KEY}:{client_ip}".encode()).hexdigest()
    item = MovieComment(
        movie_id=movie_id,
        display_name=payload.display_name,
        email=payload.email,
        comment_text=payload.comment,
        status="PENDING",
        ip_hash=ip_hash,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "status": item.status,
        "message": "Your comment has been submitted for review.",
    }
