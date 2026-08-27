"""Public discovery APIs for the movie-only V1 experience."""

from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from app.database.connection import get_db
from app.models.genre import Genre
from app.models.language import Language
from app.models.movie import Movie
from app.models.movie_metadata import (
    AlternativeTitle, ExternalId, Keyword, MovieCredit, MovieImage, MovieKeyword,
    MovieProductionCompany, MovieProductionCountry, MovieRating,
    MovieReleaseDate, Person, ProductionCompany, ProductionCountry,
)
from app.models.ott_availability import OttAvailability
from app.services.roles import ROLE_ALIASES, normalize_role

router = APIRouter(prefix="/api/v1", tags=["Discovery"])
PUBLIC_OTT_STATES = ("available", "confirmed", "announced", "upcoming", "released")

def _card(movie: Movie) -> dict:
    return {
        "id": movie.id, "tmdb_id": movie.tmdb_id, "title": movie.title,
        "original_title": movie.original_title, "overview": movie.overview,
        "release_date": movie.release_date, "poster_path": movie.poster_path,
        "backdrop_path": movie.backdrop_path, "rating": movie.vote_average,
        "vote_count": movie.vote_count, "popularity": movie.popularity,
        "language": movie.original_language, "genres": [g.name for g in movie.genres],
    }


def cards(items) -> list[dict]:
    return [_card(movie) for movie in items]


def _movie_query(db: Session):
    return db.query(Movie).options(selectinload(Movie.genres), selectinload(Movie.languages))


def _credit_match(role: str, person_value: str):
    credit = aliased(MovieCredit)
    person = aliased(Person)
    normalized = normalize_role(role)
    values = ROLE_ALIASES.get(normalized, (normalized,))
    role_match = credit.credit_type == "cast" if normalized == "actor" else or_(func.lower(credit.job).in_(values), func.lower(credit.department).in_(values))
    identity = credit.person_id == int(person_value) if person_value.isdigit() else person.name.ilike(f"%{person_value.strip()}%")
    return exists(select(credit.id).join(person, person.id == credit.person_id).where(credit.movie_id == Movie.id, role_match, identity))


def _apply_filters(query, **values):
    q = values.get("q")
    if q:
        term = f"%{q.strip()}%"
        credit_match = exists(select(MovieCredit.id).join(Person).where(MovieCredit.movie_id == Movie.id, Person.name.ilike(term)))
        keyword_match = exists(select(MovieKeyword.movie_id).join(Keyword).where(MovieKeyword.movie_id == Movie.id, Keyword.name.ilike(term)))
        alternative_match = exists(select(AlternativeTitle.id).where(AlternativeTitle.movie_id == Movie.id, AlternativeTitle.title.ilike(term)))
        query = query.filter(or_(Movie.title.ilike(term), Movie.original_title.ilike(term), alternative_match, credit_match, keyword_match))
    if values.get("language"):
        query = query.filter(or_(Movie.original_language == values["language"], Movie.languages.any(Language.iso_639_1 == values["language"])))
    if values.get("genre"):
        query = query.filter(Movie.genres.any(Genre.slug == values["genre"]))
    if values.get("year"):
        query = query.filter(func.extract("year", Movie.release_date) == values["year"])
    if values.get("rating") is not None:
        query = query.filter(Movie.vote_average >= values["rating"])
    if values.get("certification"):
        query = query.filter(Movie.release_dates.any(MovieReleaseDate.certification == values["certification"]))
    if values.get("release_status"):
        query = query.filter(func.lower(Movie.status) == values["release_status"].lower())
    if values.get("platform"):
        query = query.filter(Movie.ott_availabilities.any(OttAvailability.provider.ilike(f"%{values['platform'].replace('-', ' ')}%")))
    role_filters = {"actor": values.get("actor"), "director": values.get("director"), "writer": values.get("writer"), "cinematography": values.get("cinematographer"), "producer": values.get("producer"), "editor": values.get("editor"), "composer": values.get("composer")}
    for role_name, person_value in role_filters.items():
        if person_value:
            query = query.filter(_credit_match(role_name, person_value))
    if values.get("person"):
        query = query.filter(_credit_match(values.get("role") or "actor", str(values["person"]))) if values.get("role") else query.filter(Movie.credits.any(MovieCredit.person_id == values["person"]))
    if values.get("date_from"):
        query = query.filter(Movie.release_date >= values["date_from"])
    if values.get("date_to"):
        query = query.filter(Movie.release_date <= values["date_to"])
    return query


def _ordering(sort: str):
    ott_date = select(func.max(OttAvailability.ott_release_date)).where(OttAvailability.movie_id == Movie.id).correlate(Movie).scalar_subquery()
    return {
        "latest": Movie.release_date.desc(), "oldest": Movie.release_date.asc(),
        "rating": Movie.vote_average.desc(), "highest-rated": Movie.vote_average.desc(),
        "popular": Movie.popularity.desc(), "popularity": Movie.popularity.desc(),
        "recent": Movie.created_at.desc(), "recently-added": Movie.created_at.desc(),
        "ott-release": ott_date.desc(), "name-asc": Movie.title.asc(), "name-desc": Movie.title.desc(),
    }.get(sort, Movie.release_date.desc())


def _page(query, sort: str, page: int, page_size: int):
    total = query.order_by(None).count()
    items = query.order_by(_ordering(sort), Movie.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": cards(items), "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


@router.get("/home")
def home(db: Session = Depends(get_db)):
    base = lambda: _movie_query(db)
    today = date.today()
    ott_base = lambda: base().join(OttAvailability).filter(OttAvailability.status.in_(PUBLIC_OTT_STATES))
    language_sections = {}
    for code, name in (("ml", "Malayalam"), ("ta", "Tamil"), ("te", "Telugu"), ("hi", "Hindi"), ("kn", "Kannada")):
        language_sections[code] = {"name": name, "items": cards(base().filter(Movie.original_language == code).order_by(Movie.release_date.desc()).limit(12).all())}
    providers = db.query(OttAvailability.provider, func.max(OttAvailability.provider_logo), func.count(func.distinct(OttAvailability.movie_id))).group_by(OttAvailability.provider).order_by(func.count(func.distinct(OttAvailability.movie_id)).desc()).all()
    return {
        "trending": cards(base().order_by(Movie.popularity.desc()).limit(12).all()),
        "popular": cards(base().order_by(Movie.vote_average.desc(), Movie.vote_count.desc()).limit(12).all()),
        "latest_theatrical": cards(base().filter(Movie.release_date <= today).order_by(Movie.release_date.desc()).limit(12).all()),
        "upcoming_theatrical": cards(base().filter(Movie.release_date > today).order_by(Movie.release_date).limit(12).all()),
        "recently_added": cards(base().order_by(Movie.created_at.desc()).limit(12).all()),
        "upcoming_ott": cards(ott_base().filter(OttAvailability.ott_release_date > today).order_by(OttAvailability.ott_release_date).limit(12).all()),
        "recent_ott": cards(ott_base().filter(OttAvailability.ott_release_date <= today).order_by(OttAvailability.ott_release_date.desc()).limit(12).all()),
        "language_sections": language_sections,
        "languages": [{"code": code, "name": value["name"]} for code, value in language_sections.items()],
        "genres": [{"slug": item.slug, "name": item.name} for item in db.query(Genre).order_by(Genre.name).all()],
        "platforms": [{"slug": provider.lower().replace(" ", "-"), "name": provider, "logo": logo, "movie_count": count} for provider, logo, count in providers],
    }


@router.get("/discover")
def discover(
    q: str | None = Query(None, max_length=200), language: str | None = None, genre: str | None = None,
    person: int | None = None, role: str | None = None, year: int | None = None,
    rating: float | None = Query(None, ge=0, le=10), certification: str | None = None,
    release_status: str | None = None, platform: str | None = None,
    actor: str | None = None, director: str | None = None, writer: str | None = None,
    cinematographer: str | None = None, producer: str | None = None, editor: str | None = None,
    composer: str | None = None, date_from: date | None = None, date_to: date | None = None,
    sort: str = "latest", page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "start date must be on or before end date")
    query = _apply_filters(_movie_query(db), q=q, language=language, genre=genre, person=person, role=role, year=year, rating=rating, certification=certification, release_status=release_status, platform=platform, actor=actor, director=director, writer=writer, cinematographer=cinematographer, producer=producer, editor=editor, composer=composer, date_from=date_from, date_to=date_to)
    result = _page(query, sort, page, page_size)
    result["filters"] = {
        "genres": [{"slug": x.slug, "name": x.name} for x in db.query(Genre).order_by(Genre.name)],
        "languages": [{"code": x.iso_639_1, "name": x.english_name} for x in db.query(Language).order_by(Language.english_name)],
        "platforms": [x[0] for x in db.query(OttAvailability.provider).distinct().order_by(OttAvailability.provider)],
        "roles": list(ROLE_ALIASES),
    }
    return result


@router.get("/search")
def global_search(q: str = Query("", max_length=200), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    if not q.strip():
        return {"movies": {"items": [], "total": 0}, "people": {"items": [], "total": 0}, "page": page, "page_size": page_size}
    movie_query = _apply_filters(_movie_query(db), q=q)
    movie_total = movie_query.count()
    movies = movie_query.order_by(Movie.popularity.desc()).offset((page - 1) * page_size).limit(page_size).all()
    people_query = db.query(Person).filter(Person.name.ilike(f"%{q.strip()}%"))
    people_total = people_query.count()
    people = people_query.order_by(Person.name).offset((page - 1) * page_size).limit(page_size).all()
    return {"movies": {"items": cards(movies), "total": movie_total}, "people": {"items": [{"id": x.id, "name": x.name, "profile_path": x.profile_path, "department": x.known_for_department} for x in people], "total": people_total}, "page": page, "page_size": page_size}


@router.get("/ott")
def ott_landing(db: Session = Depends(get_db)):
    today = date.today()
    valid = (OttAvailability.status.in_(PUBLIC_OTT_STATES),)
    provider_rows = db.query(OttAvailability.provider, func.max(OttAvailability.provider_logo), func.count(func.distinct(OttAvailability.movie_id))).filter(*valid).group_by(OttAvailability.provider).order_by(func.count(func.distinct(OttAvailability.movie_id)).desc()).all()
    movie_query = _movie_query(db).join(OttAvailability).filter(*valid)
    return {
        "platforms": [{"slug": name.lower().replace(" ", "-"), "name": name, "logo": logo, "movie_count": count} for name, logo, count in provider_rows],
        "upcoming": cards(movie_query.filter(OttAvailability.ott_release_date > today).order_by(OttAvailability.ott_release_date).limit(24).all()),
        "recent": cards(movie_query.filter(OttAvailability.ott_release_date <= today).order_by(OttAvailability.ott_release_date.desc()).limit(24).all()),
        "confirmed": cards(movie_query.filter(OttAvailability.status.in_(["confirmed", "announced"]), OttAvailability.confidence >= 85).order_by(OttAvailability.updated_at.desc()).limit(24).all()),
    }


@router.get("/ott/{platform}")
def ott_platform(platform: str, sort: str = "ott-release", page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100), db: Session = Depends(get_db)):
    provider = platform.replace("-", " ")
    matching = db.query(OttAvailability.provider).filter(OttAvailability.provider.ilike(provider)).first()
    canonical = matching[0] if matching else provider
    query = _movie_query(db).filter(Movie.ott_availabilities.any(OttAvailability.provider.ilike(canonical)))
    result = _page(query, sort, page, page_size)
    result.update({"platform": canonical, "upcoming": [], "recent": [], "available": result["items"]})
    today = date.today()
    result["upcoming"] = cards(query.join(OttAvailability).filter(OttAvailability.provider.ilike(canonical), OttAvailability.ott_release_date > today).order_by(OttAvailability.ott_release_date).limit(12).all())
    result["recent"] = cards(query.join(OttAvailability).filter(OttAvailability.provider.ilike(canonical), OttAvailability.ott_release_date <= today).order_by(OttAvailability.ott_release_date.desc()).limit(12).all())
    return result


@router.get("/people/{person_id}")
def person_detail(person_id: int, sort: str = "newest", credit_type: str = "all", role: str | None = None, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    query = db.query(MovieCredit).filter(MovieCredit.person_id == person_id).join(Movie).options(selectinload(MovieCredit.movie).selectinload(Movie.genres))
    if credit_type in {"cast", "crew"}:
        query = query.filter(MovieCredit.credit_type == credit_type)
    normalized = normalize_role(role)
    if normalized:
        aliases = ROLE_ALIASES.get(normalized, (normalized,))
        query = query.filter(or_(func.lower(MovieCredit.job).in_(aliases), func.lower(MovieCredit.department).in_(aliases), and_(normalized == "actor", MovieCredit.credit_type == "cast")))
    credits = query.order_by(Movie.release_date.asc() if sort == "oldest" else Movie.release_date.desc()).all()
    return {
        "id": person.id, "tmdb_id": person.tmdb_id, "name": person.name,
        "profile_path": person.profile_path, "department": person.known_for_department,
        "roles": sorted({normalize_role(x.job or x.department or x.credit_type) for x in credits if normalize_role(x.job or x.department or x.credit_type)}),
        "filmography": [{"movie": _card(x.movie), "character": x.character, "job": x.job, "department": x.department, "credit_type": x.credit_type, "normalized_role": normalize_role(x.job or x.department or x.credit_type)} for x in credits],
    }


@router.get("/calendar/{period}")
def calendar(period: str, db: Session = Depends(get_db)):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    this_month = today.replace(day=1)
    previous_month = (this_month - timedelta(days=1)).replace(day=1)
    next_month = this_month.replace(day=monthrange(this_month.year, this_month.month)[1]) + timedelta(days=1)
    after_next = next_month.replace(day=monthrange(next_month.year, next_month.month)[1]) + timedelta(days=1)
    ranges = {"previous-week": (monday - timedelta(days=7), monday), "this-week": (monday, monday + timedelta(days=7)), "next-week": (monday + timedelta(days=7), monday + timedelta(days=14)), "previous-month": (previous_month, this_month), "this-month": (this_month, next_month), "next-month": (next_month, after_next)}
    if period not in ranges:
        raise HTTPException(404, "Unknown calendar period")
    start, end = ranges[period]
    return {"period": period, "start": start, "end": end - timedelta(days=1), "items": cards(_movie_query(db).filter(Movie.release_date >= start, Movie.release_date < end).order_by(Movie.release_date).all())}


@router.get("/movies/{movie_id}/detail")
def movie_detail(movie_id: int, db: Session = Depends(get_db)):
    movie = db.query(Movie).options(selectinload(Movie.genres), selectinload(Movie.languages), selectinload(Movie.ott_availabilities)).filter(Movie.id == movie_id).first()
    if not movie:
        raise HTTPException(404, "Movie not found")
    credits = db.query(MovieCredit).filter_by(movie_id=movie_id).options(selectinload(MovieCredit.person)).order_by(MovieCredit.credit_type, MovieCredit.cast_order, MovieCredit.department, MovieCredit.job).all()
    images = db.query(MovieImage).filter_by(movie_id=movie_id).order_by(MovieImage.image_type, MovieImage.is_primary.desc()).all()
    releases = db.query(MovieReleaseDate).filter_by(movie_id=movie_id).order_by(MovieReleaseDate.release_date).all()
    companies = db.query(ProductionCompany).join(MovieProductionCompany, MovieProductionCompany.production_company_id == ProductionCompany.id).filter(MovieProductionCompany.movie_id == movie_id).all()
    countries = db.query(ProductionCountry).join(MovieProductionCountry, MovieProductionCountry.production_country_id == ProductionCountry.id).filter(MovieProductionCountry.movie_id == movie_id).all()
    keywords = db.query(Keyword).join(MovieKeyword, MovieKeyword.keyword_id == Keyword.id).filter(MovieKeyword.movie_id == movie_id).order_by(Keyword.name).all()
    certification = next((x.certification for x in releases if x.country == "IN" and x.certification), None) or next((x.certification for x in releases if x.certification), None)
    grouped_crew = {}
    for credit in credits:
        normalized = normalize_role(credit.job or credit.department)
        if credit.credit_type == "crew" and normalized:
            grouped_crew.setdefault(normalized, []).append({"person_id": credit.person_id, "name": credit.person.name, "profile_path": credit.person.profile_path, "job": credit.job, "department": credit.department})
    grouped_releases = {"theatrical": [], "digital": [], "streaming": [], "other": []}
    for item in releases:
        release_type = item.release_type.lower()
        group = "theatrical" if release_type in {"2", "3", "limited theatrical", "theatrical"} else "digital" if release_type in {"4", "digital"} else "streaming" if release_type in {"ott", "streaming"} else "other"
        grouped_releases[group].append({"country": item.country, "date": item.release_date, "type": item.release_type, "certification": item.certification, "note": item.note})
    return {
        "movie": _card(movie) | {
            "tagline": movie.tagline, "runtime_minutes": movie.runtime_minutes, "status": movie.status,
            "certification": certification, "budget": movie.budget, "revenue": movie.revenue,
            "collection": {"tmdb_id": movie.collection_tmdb_id, "name": movie.collection_name, "poster_path": movie.collection_poster_path, "backdrop_path": movie.collection_backdrop_path} if movie.collection_name else None,
            "original_language": movie.original_language,
            "spoken_languages": [{"code": x.iso_639_1, "name": x.english_name} for x in movie.languages],
            "production_countries": [{"code": x.iso_3166_1, "name": x.name} for x in countries],
            "production_companies": [{"name": x.name, "logo": x.logo_path, "country": x.origin_country} for x in companies],
            "ott": [{"provider": x.provider, "logo": x.provider_logo, "watch_type": x.watch_type, "release_date": x.ott_release_date, "country": x.country, "source": x.source_type, "source_url": x.source_url, "confidence": x.confidence, "verification_state": x.status, "last_checked": x.last_checked} for x in movie.ott_availabilities],
        },
        "cast": [{"person_id": x.person_id, "name": x.person.name, "profile_path": x.person.profile_path, "character": x.character, "order": x.cast_order} for x in credits if x.credit_type == "cast"],
        "crew": [{"person_id": x.person_id, "name": x.person.name, "profile_path": x.person.profile_path, "job": x.job, "department": x.department, "normalized_role": normalize_role(x.job or x.department)} for x in credits if x.credit_type == "crew"],
        "crew_by_role": grouped_crew,
        "images": [{"type": x.image_type, "url": x.local_path or x.original_url, "language": x.language, "width": x.width, "height": x.height, "aspect_ratio": x.aspect_ratio, "primary": x.is_primary} for x in images],
        "releases": [{"country": x.country, "date": x.release_date, "type": x.release_type, "certification": x.certification, "note": x.note} for x in releases],
        "release_groups": grouped_releases,
        "ratings": ([{"source": "TMDB", "rating": movie.vote_average, "votes": movie.vote_count}] if movie.vote_average is not None else []) + [{"source": x.source, "rating": x.rating, "votes": x.vote_count} for x in db.query(MovieRating).filter_by(movie_id=movie_id).all()],
        "keywords": [x.name for x in keywords],
        "alternative_titles": [{"title": x.title, "country": x.country, "type": x.title_type} for x in db.query(AlternativeTitle).filter_by(movie_id=movie_id).order_by(AlternativeTitle.country, AlternativeTitle.title).all()],
        "external_ids": [{"provider": "tmdb", "id": str(movie.tmdb_id), "url": f"https://www.themoviedb.org/movie/{movie.tmdb_id}"}] + [{"provider": x.provider, "id": x.external_id, "url": x.source_url} for x in db.query(ExternalId).filter_by(movie_id=movie_id).all()],
    }
