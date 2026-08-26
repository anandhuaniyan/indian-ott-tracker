# Deployment

Copy `.env.example` to `.env`, supply TMDB and strong secret/admin values, then run `docker compose up --build`. The API is on port 8000, frontend on 5173, PostgreSQL on 5433 and Redis on 6380. API startup runs only additive Alembic migrations and keeps the named PostgreSQL volume.

Set `SITE_URL` to the public HTTPS URL before indexing. Submit `/sitemap.xml` in Google Search Console and set its verification token in the frontend/deployment configuration. Configure analytics and AdSense IDs only after those accounts are approved; `ads.txt` stays empty until a publisher ID is configured.

Admin endpoints require `X-Admin-Key`. Do not put that key in frontend configuration or client code.
