# Indian OTT Tracker

Indian OTT Tracker is a movie-only discovery site for Malayalam, Tamil, Telugu, Hindi, Kannada and future Indian-language catalogues. It builds on the existing TMDB-backed FastAPI/PostgreSQL data pipeline and adds a responsive React frontend, people discovery, SEO endpoints, protected operations and a privacy-conscious movie request flow.

## Run locally

1. Copy `.env.example` to `.env` and set `TMDB_API_KEY`, `SECRET_KEY`, and `ADMIN_API_KEY`.
2. Run `docker compose up --build`.
3. Open `http://localhost:5173`; API documentation is at `http://localhost:8000/docs`.

The API applies Alembic migrations on startup and uses named volumes, so existing movie data is retained. Do not remove the PostgreSQL volume when updating.

## Included routes

- Public discovery: `/api/v1/home`, `/api/v1/discover`, `/api/v1/people/{id}` and `/api/v1/calendar/{period}`.
- Movie pages consume the existing `/movies/{id}` metadata, cast, crew, artwork, release, ratings and external ID endpoints.
- Movie requests: `POST /api/v1/movie-requests`.
- Protected operations: `/api/v1/admin/*` and legacy enrichment/sync mutations require an `X-Admin-Key` header.

See [deployment documentation](docs/DEPLOYMENT.md), [architecture](docs/ARCHITECTURE.md), [OTT research policy](docs/OTT_RESEARCH.md), and [SEO notes](docs/SEO.md).
