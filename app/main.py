from fastapi import FastAPI, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import logging
from xml.sax.saxutils import escape

from app.api.movies import router as movie_router
from app.api.v1.public import router as public_router
from app.api.v1.operations import router as operations_router
from app.api.v1.admin import router as admin_router
from app.database.connection import get_db
from app.config.settings import settings

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


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
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https://image.tmdb.org; style-src 'self'; script-src 'self' https://www.googletagmanager.com https://pagead2.googlesyndication.com; connect-src 'self' https://www.google-analytics.com; frame-src https://googleads.g.doubleclick.net; base-uri 'self'; form-action 'self'; object-src 'none'"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

media_root = Path(settings.MEDIA_ROOT)
media_root.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_root), name="media")
storage_root = Path("storage")
storage_root.mkdir(parents=True, exist_ok=True)
app.mount("/storage", StaticFiles(directory=storage_root), name="storage")


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

@app.get("/robots.txt", include_in_schema=False)
def robots():
    return Response("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin\nSitemap: " + settings.SITE_URL.rstrip("/") + "/sitemap.xml\n", media_type="text/plain")

@app.get("/ads.txt", include_in_schema=False)
def ads():
    return Response(f"google.com, {settings.ADSENSE_PUBLISHER_ID}, DIRECT, f08c47fec0942fa0\n" if settings.ADSENSE_PUBLISHER_ID else "", media_type="text/plain")

@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(db=Depends(get_db)):
    from app.models.movie import Movie
    from app.models.movie_metadata import Person
    from app.models.genre import Genre
    from app.models.ott_availability import OttAvailability
    base = settings.SITE_URL.rstrip("/")
    static = ["/", "/discover", "/search", "/ott", "/request-movie", "/about", "/contact", "/privacy", "/terms", "/cookies"]
    static += [f"/calendar/{period}" for period in ("previous-week", "this-week", "next-week", "previous-month", "this-month", "next-month")]
    static += [f"/languages/{code}" for code in ("ml", "ta", "te", "hi", "kn")]
    static += [f"/genres/{row.slug}" for row in db.query(Genre.slug).order_by(Genre.slug)]
    static += [f"/ott/{row[0].lower().replace(' ', '-')}" for row in db.query(OttAvailability.provider).distinct().order_by(OttAvailability.provider)]
    rows = [f"<url><loc>{escape(base + path)}</loc></url>" for path in static]
    rows += [f"<url><loc>{escape(base + '/movies/' + str(m.id))}</loc>{f'<lastmod>{m.updated_at.date().isoformat()}</lastmod>' if m.updated_at else ''}</url>" for m in db.query(Movie.id, Movie.updated_at).yield_per(1000)]
    rows += [f"<url><loc>{escape(base + '/people/' + str(p.id))}</loc></url>" for p in db.query(Person.id).yield_per(1000)]
    return Response("<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + "".join(rows) + "</urlset>", media_type="application/xml")
