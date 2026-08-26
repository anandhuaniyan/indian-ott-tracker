from fastapi import FastAPI, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api.movies import router as movie_router
from app.api.v1.public import router as public_router
from app.api.v1.operations import router as operations_router
from app.api.v1.admin import router as admin_router
from app.database.connection import get_db
from app.config.settings import settings


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
    return response

media_root = Path(settings.MEDIA_ROOT)
media_root.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_root), name="media")


# Frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.FRONTEND_ORIGINS.split(",")],
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
    base = settings.SITE_URL.rstrip("/")
    rows = [f"<url><loc>{base}/</loc></url>"]
    rows += [f"<url><loc>{base}/movies/{m.id}</loc><lastmod>{m.updated_at.date().isoformat() if m.updated_at else ''}</lastmod></url>" for m in db.query(Movie.id, Movie.updated_at).yield_per(1000)]
    rows += [f"<url><loc>{base}/people/{p.id}</loc></url>" for p in db.query(Person.id).yield_per(1000)]
    return Response("<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + "".join(rows) + "</urlset>", media_type="application/xml")
