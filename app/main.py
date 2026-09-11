from fastapi import FastAPI, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import logging

from app.api.movies import router as movie_router
from app.api.v1.public import router as public_router
from app.api.v1.operations import router as operations_router
from app.api.v1.admin import router as admin_router
from app.api.v1.deep_search import router as deep_search_router
from app.database.connection import get_db
from app.config.settings import settings
from app.seo import (
    ads_txt,
    robots_txt,
    sitemapindex,
    static_urls,
    url_row,
    urlset,
    people_file_count,
    SITEMAP_PEOPLE_PER_FILE,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
_omdb_missing = [
    name for name, present in (
        ("IMDB_RATING_PROVIDER", settings.IMDB_RATING_PROVIDER.strip().lower() == "omdb"),
        ("IMDB_RATING_API_URL", bool(settings.IMDB_RATING_API_URL)),
        ("IMDB_RATING_API_KEY", bool(settings.IMDB_RATING_API_KEY)),
    ) if not present
]
_startup_log = logging.getLogger(__name__)
if _omdb_missing:
    _startup_log.warning(
        "OMDb startup configuration: NOT_CONFIGURED; missing=%s",
        ",".join(_omdb_missing),
    )
else:
    _startup_log.info("OMDb startup configuration: READY")


frontend_origins = [origin.strip().rstrip("/") for origin in settings.FRONTEND_ORIGINS.split(",") if origin.strip()]
if settings.ENVIRONMENT == "production" and "*" in frontend_origins:
    raise RuntimeError("FRONTEND_ORIGINS cannot contain '*' when credentials are enabled in production")

app = FastAPI(
    title="Indian OTT Tracker",
    version="0.1.0"
)

@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' https://i.ytimg.com data: blob: https://image.tmdb.org https://www.google-analytics.com https://pagead2.googlesyndication.com https://googleads.g.doubleclick.net; style-src 'self'; script-src 'self' https://www.googletagmanager.com https://pagead2.googlesyndication.com; connect-src 'self' https://www.google-analytics.com https://analytics.google.com https://googleads.g.doubleclick.net https://pagead2.googlesyndication.com; frame-src https://www.youtube-nocookie.com https://googleads.g.doubleclick.net; base-uri 'self'; form-action 'self'; object-src 'none'"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith(("/api/v1/admin", "/api/v1/integrations/pinterest")):
        response.headers["Cache-Control"] = "no-store, private"
    return response

media_root = Path(settings.MEDIA_ROOT)
media_root.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_root), name="media")


# Frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "project": "Indian OTT Tracker",
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


app.include_router(movie_router)
app.include_router(public_router)
app.include_router(operations_router)
app.include_router(admin_router)
app.include_router(deep_search_router)

@app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
def robots():
    return Response(
        robots_txt(settings.SITE_URL),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache"},
    )


@app.api_route("/ads.txt", methods=["GET", "HEAD"], include_in_schema=False)
def ads():
    return Response(ads_txt(settings.ADSENSE_PUBLISHER_ID), media_type="text/plain")


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_index(db=Depends(get_db)):
    from sqlalchemy import func
    from app.models.movie_metadata import Person

    base = settings.SITE_URL.rstrip("/")
    people = db.query(func.count(Person.id)).scalar() or 0
    references = [f"{base}/sitemaps/static.xml", f"{base}/sitemaps/movies.xml"]
    references += [
        f"{base}/sitemaps/people-{index}.xml"
        for index in range(1, people_file_count(people) + 1)
    ]
    body = "".join(
        f"<sitemap><loc>{ref}</loc></sitemap>" for ref in references
    )
    return Response(sitemapindex(body), media_type="application/xml")


@app.api_route("/sitemaps/static.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_static(db=Depends(get_db)):
    rows = "".join(url_row(loc) for loc in static_urls(settings.SITE_URL, db))
    return Response(urlset(rows), media_type="application/xml")


@app.api_route("/sitemaps/movies.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_movies(db=Depends(get_db)):
    from app.models.movie import Movie

    base = settings.SITE_URL.rstrip("/")
    rows = []
    for movie_id, updated_at in db.query(Movie.id, Movie.updated_at).order_by(Movie.id).yield_per(2000):
        lastmod = updated_at.date().isoformat() if updated_at else None
        rows.append(url_row(f"{base}/movies/{movie_id}", lastmod))
    return Response(urlset("".join(rows)), media_type="application/xml")


@app.api_route("/sitemaps/people-{page}.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_people(page: int, db=Depends(get_db)):
    from app.models.movie_metadata import Person

    base = settings.SITE_URL.rstrip("/")
    page = max(1, page)
    rows = []
    for (person_id,) in (
        db.query(Person.id)
        .order_by(Person.id)
        .offset((page - 1) * SITEMAP_PEOPLE_PER_FILE)
        .limit(SITEMAP_PEOPLE_PER_FILE)
    ):
        rows.append(url_row(f"{base}/people/{person_id}"))
    return Response(urlset("".join(rows)), media_type="application/xml")
