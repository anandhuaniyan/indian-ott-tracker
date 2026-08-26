from datetime import date, timedelta
from calendar import monthrange
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload
from app.database.connection import get_db
from app.models.movie import Movie
from app.models.movie_metadata import MovieCredit, Person, Keyword, MovieKeyword, MovieImage, MovieRating, MovieReleaseDate, ExternalId
from app.models.ott_availability import OttAvailability
from app.models.genre import Genre
from app.models.language import Language

router = APIRouter(prefix="/api/v1", tags=["Discovery"])

def cards(items):
    return [{"id": m.id, "title": m.title, "original_title": m.original_title, "overview": m.overview, "release_date": m.release_date, "poster_path": m.poster_path, "backdrop_path": m.backdrop_path, "rating": m.vote_average, "popularity": m.popularity, "language": m.original_language, "genres": [g.name for g in m.genres]} for m in items]

@router.get("/home")
def home(db: Session = Depends(get_db)):
    base = lambda: db.query(Movie).options(selectinload(Movie.genres), selectinload(Movie.languages))
    today = date.today()
    return {"trending": cards(base().order_by(Movie.popularity.desc()).limit(12).all()), "popular": cards(base().order_by(Movie.vote_average.desc(), Movie.vote_count.desc()).limit(12).all()), "latest": cards(base().filter(Movie.release_date <= today).order_by(Movie.release_date.desc()).limit(12).all()), "upcoming": cards(base().filter(Movie.release_date > today).order_by(Movie.release_date).limit(12).all()), "recently_added": cards(base().order_by(Movie.created_at.desc()).limit(12).all()), "languages": [{"code": x.iso_639_1, "name": x.name} for x in db.query(Language).order_by(Language.name).all()], "genres": [{"slug": x.slug, "name": x.name} for x in db.query(Genre).order_by(Genre.name).all()]}

@router.get("/discover")
def discover(q: str | None = Query(None, max_length=200), language: str | None = None, genre: str | None = None, person: int | None = None, role: str | None = None, year: int | None = None, platform: str | None = None, date_from: date | None = None, date_to: date | None = None, sort: str = "latest", page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100), db: Session = Depends(get_db)):
    query = db.query(Movie).options(selectinload(Movie.genres), selectinload(Movie.languages)).distinct()
    if q:
        term = f"%{q.strip()}%"
        query = query.outerjoin(MovieCredit).outerjoin(Person).outerjoin(MovieKeyword, MovieKeyword.movie_id == Movie.id).outerjoin(Keyword, Keyword.id == MovieKeyword.keyword_id).filter(or_(Movie.title.ilike(term), Movie.original_title.ilike(term), Person.name.ilike(term), Keyword.name.ilike(term)))
    if language: query = query.join(Movie.languages).filter(Language.iso_639_1 == language)
    if genre: query = query.join(Movie.genres).filter(Genre.slug == genre)
    if person:
        query = query.join(MovieCredit).filter(MovieCredit.person_id == person)
        if role: query = query.filter(or_(MovieCredit.credit_type == role, MovieCredit.job.ilike(f"%{role}%")))
    if year: query = query.filter(func.extract("year", Movie.release_date) == year)
    if platform: query = query.join(OttAvailability).filter(OttAvailability.provider.ilike(f"%{platform}%"))
    if date_from: query = query.filter(Movie.release_date >= date_from)
    if date_to: query = query.filter(Movie.release_date <= date_to)
    ordering = {"rating": Movie.vote_average.desc(), "popular": Movie.popularity.desc(), "oldest": Movie.release_date.asc(), "recent": Movie.created_at.desc()}.get(sort, Movie.release_date.desc())
    total = query.count()
    return {"items": cards(query.order_by(ordering).offset((page - 1) * page_size).limit(page_size).all()), "total": total, "page": page, "page_size": page_size}

@router.get("/people/{person_id}")
def person_detail(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if not person: raise HTTPException(404, "Person not found")
    credits = db.query(MovieCredit).filter(MovieCredit.person_id == person_id).join(Movie).options(selectinload(MovieCredit.movie).selectinload(Movie.genres)).order_by(Movie.release_date.desc()).all()
    return {"id": person.id, "tmdb_id": person.tmdb_id, "name": person.name, "profile_path": person.profile_path, "department": person.known_for_department, "filmography": [{"movie": cards([c.movie])[0], "role": c.character or c.job or c.credit_type, "department": c.department} for c in credits]}

@router.get("/calendar/{period}")
def calendar(period: str, db: Session = Depends(get_db)):
    today = date.today(); monday = today - timedelta(days=today.weekday()); this_month = today.replace(day=1)
    previous_month = (this_month - timedelta(days=1)).replace(day=1); next_month = (this_month.replace(day=28) + timedelta(days=4)).replace(day=1); after_next = (next_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    ranges = {"previous-week": (monday - timedelta(days=7), monday), "this-week": (monday, monday + timedelta(days=7)), "next-week": (monday + timedelta(days=7), monday + timedelta(days=14)), "previous-month": (previous_month, this_month), "this-month": (this_month, next_month), "next-month": (next_month, after_next)}
    if period not in ranges: raise HTTPException(404, "Unknown calendar period")
    start, end = ranges[period]
    return {"start": start, "end": end, "items": cards(db.query(Movie).options(selectinload(Movie.genres)).filter(Movie.release_date >= start, Movie.release_date < end).order_by(Movie.release_date).all())}

@router.get("/movies/{movie_id}/detail")
def movie_detail(movie_id: int, db: Session = Depends(get_db)):
    movie = db.query(Movie).options(selectinload(Movie.genres), selectinload(Movie.languages), selectinload(Movie.ott_availabilities)).filter(Movie.id == movie_id).first()
    if not movie: raise HTTPException(404, "Movie not found")
    cast = db.query(MovieCredit).filter_by(movie_id=movie_id, credit_type="cast").order_by(MovieCredit.cast_order).all()
    crew = db.query(MovieCredit).filter_by(movie_id=movie_id, credit_type="crew").order_by(MovieCredit.department, MovieCredit.job).all()
    return {"movie": cards([movie])[0] | {"tmdb_id": movie.tmdb_id, "tagline": movie.tagline, "runtime_minutes": movie.runtime_minutes, "status": movie.status, "budget": movie.budget, "revenue": movie.revenue, "spoken_languages": [{"code": x.iso_639_1, "name": x.name} for x in movie.languages], "ott": [{"provider": x.provider, "logo": x.provider_logo, "watch_type": x.watch_type, "release_date": x.ott_release_date, "url": x.source_url, "confidence": x.confidence, "status": x.status} for x in movie.ott_availabilities]}, "cast": [{"person_id": x.person_id, "name": x.person.name, "profile_path": x.person.profile_path, "character": x.character} for x in cast], "crew": [{"person_id": x.person_id, "name": x.person.name, "profile_path": x.person.profile_path, "job": x.job, "department": x.department} for x in crew], "images": [{"type": x.image_type, "url": x.local_path or x.original_url, "language": x.language} for x in db.query(MovieImage).filter_by(movie_id=movie_id).all()], "releases": [{"country": x.country, "date": x.release_date, "type": x.release_type, "certification": x.certification, "note": x.note} for x in db.query(MovieReleaseDate).filter_by(movie_id=movie_id).all()], "ratings": [{"source": x.source, "rating": x.rating, "votes": x.vote_count} for x in db.query(MovieRating).filter_by(movie_id=movie_id).all()], "external_ids": [{"provider": x.provider, "id": x.external_id, "url": x.source_url} for x in db.query(ExternalId).filter_by(movie_id=movie_id).all()]}
