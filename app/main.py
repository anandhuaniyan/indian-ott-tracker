from fastapi import FastAPI, Response, Depends, HTTPException, Path as ApiPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import logging

from app.api.movies import router as movie_router
from app.api.v1.public import router as public_router
from app.api.v1.operations import router as operations_router
from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.deep_search import router as deep_search_router
from app.api.v1.pinterest import callback_router as pinterest_callback_router
from app.api.v1.pinterest import router as pinterest_router
from app.api.v1.render import router as render_router
from app.api.v1.ai_activity import router as ai_activity_router
from app.api.v1.support import admin_router as support_admin_router
from app.api.v1.support import router as support_router
from app.api.v1.cloudflare_maintenance import router as cloudflare_maintenance_router
from app.database.connection import get_db
from app.config.settings import settings
from app.seo import (
    ads_txt,
    robots_txt,
    sitemapindex,
    sitemap_row,
    static_urls,
    url_row,
    urlset,
    sitemap_file_count,
    SITEMAP_URLS_PER_FILE,
    indexable_people_query,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
_omdb_missing = [
    name for name, present in (
        ("IMDB_RATING_PROVIDER", settings.IMDB_RATING_PROVIDER.strip().lower() == "omdb"),
        ("IMDB_RATING_API_URL", bool(settings.IMDB_RATING_API_URL)),
        ("OMDB_API_KEYS", bool((settings.OMDB_API_KEYS or settings.IMDB_RATING_API_KEY).strip())),
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
    if request.url.path.startswith(("/api/v1/admin", "/api/v1/auth", "/api/v1/support", "/api/v1/integrations/pinterest")):
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
app.include_router(auth_router)
app.include_router(deep_search_router)
app.include_router(pinterest_router)
app.include_router(pinterest_callback_router)
app.include_router(render_router)
app.include_router(ai_activity_router)
app.include_router(support_router)
app.include_router(support_admin_router)
app.include_router(cloudflare_maintenance_router)


@app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
def robots():
    return Response(
        robots_txt(settings.SITE_URL),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache"},
    )


SITEMAP_HEADERS = {"Cache-Control": "public, max-age=900"}


@app.api_route("/ads.txt", methods=["GET", "HEAD"], include_in_schema=False)
def ads():
    return Response(ads_txt(settings.ADSENSE_PUBLISHER_ID), media_type="text/plain")


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_index(db=Depends(get_db)):
    from sqlalchemy import func
    from app.models.movie import Movie
    from app.models.movie_metadata import Person

    base = settings.SITE_URL.rstrip("/")
    movies = db.query(func.count(Movie.id)).filter(
        Movie.safety_public_visibility == "FULL"
    ).scalar() or 0
    people = indexable_people_query(db).count()
    references = [f"{base}/sitemap-static.xml"]
    from app.models.editorial import EditorialArticle
    if db.query(EditorialArticle.id).filter(EditorialArticle.status == "PUBLISHED", EditorialArticle.reviewed_at.is_not(None), EditorialArticle.is_indexable.is_(True)).first():
        references.append(f"{base}/sitemap-updates.xml")
    references += [
        f"{base}/sitemap-movies-{index}.xml"
        for index in range(1, sitemap_file_count(movies) + 1)
    ]
    references += [
        f"{base}/sitemap-people-{index}.xml"
        for index in range(1, sitemap_file_count(people) + 1)
    ]
    body = "".join(sitemap_row(ref) for ref in references)
    return Response(sitemapindex(body), media_type="application/xml", headers=SITEMAP_HEADERS)


@app.api_route("/sitemap-static.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_static(db=Depends(get_db)):
    rows = "".join(url_row(loc) for loc in static_urls(settings.SITE_URL, db))
    return Response(urlset(rows), media_type="application/xml", headers=SITEMAP_HEADERS)


@app.api_route("/sitemap-updates.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_updates(db=Depends(get_db)):
    from app.models.editorial import EditorialArticle
    base = settings.SITE_URL.rstrip("/")
    articles = db.query(EditorialArticle).filter(
        EditorialArticle.status == "PUBLISHED",
        EditorialArticle.reviewed_at.is_not(None),
        EditorialArticle.is_indexable.is_(True),
    ).order_by(EditorialArticle.slug).all()
    rows = "".join(url_row(f"{base}/updates/{row.slug}", (row.updated_at or row.published_at).date().isoformat() if (row.updated_at or row.published_at) else None) for row in articles)
    return Response(urlset(rows), media_type="application/xml", headers=SITEMAP_HEADERS)


@app.api_route("/sitemap-movies-{page}.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_movies(page: int = ApiPath(ge=1), db=Depends(get_db)):
    from sqlalchemy import func
    from app.models.movie import Movie

    base = settings.SITE_URL.rstrip("/")
    count = db.query(func.count(Movie.id)).filter(
        Movie.safety_public_visibility == "FULL"
    ).scalar() or 0
    if page > sitemap_file_count(count):
        raise HTTPException(404, "Movie sitemap chunk not found")
    rows = []
    query = db.query(Movie.id, Movie.updated_at).filter(
        Movie.safety_public_visibility == "FULL"
    ).order_by(Movie.id).offset((page - 1) * SITEMAP_URLS_PER_FILE).limit(SITEMAP_URLS_PER_FILE)
    for movie_id, updated_at in query:
        lastmod = updated_at.date().isoformat() if updated_at else None
        rows.append(url_row(f"{base}/movies/{movie_id}", lastmod))
    return Response(urlset("".join(rows)), media_type="application/xml", headers=SITEMAP_HEADERS)


@app.api_route("/sitemap-people-{page}.xml", methods=["GET", "HEAD"], include_in_schema=False)
def sitemap_people(page: int = ApiPath(ge=1), db=Depends(get_db)):
    from sqlalchemy import func
    from app.models.movie_metadata import Person

    base = settings.SITE_URL.rstrip("/")
    query = indexable_people_query(db)
    count = query.count()
    if page > sitemap_file_count(count):
        raise HTTPException(404, "People sitemap chunk not found")
    rows = []
    for person_id, updated_at in (
        query.with_entities(Person.id, Person.updated_at)
        .order_by(Person.id)
        .offset((page - 1) * SITEMAP_URLS_PER_FILE)
        .limit(SITEMAP_URLS_PER_FILE)
    ):
        lastmod = updated_at.date().isoformat() if updated_at else None
        rows.append(url_row(f"{base}/people/{person_id}", lastmod))
    return Response(urlset("".join(rows)), media_type="application/xml", headers=SITEMAP_HEADERS)
