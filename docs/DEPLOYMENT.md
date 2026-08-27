# Deployment

Copy `.env.example` to `.env` and configure database, Redis, TMDB, strong admin/session secrets, site origins/URL and any optional integrations. Configure `IMDB_RATING_PROVIDER=omdb` plus the approved API URL/key to collect IMDb-compatible ratings; without it the site truthfully shows ratings as unavailable. Generate `ADMIN_PASSWORD_HASH` using the command shown in `.env.example`. Use HTTPS and set `ENVIRONMENT=production` to enable Secure cookies and HSTS.

`docker compose up --build -d` starts PostgreSQL, Redis, API, frontend, Celery worker and Beat. pgAdmin is isolated behind the optional `tools` profile. PostgreSQL, Redis, API, frontend and worker have health checks; API waits for PostgreSQL/Redis and frontend waits for API health. PostgreSQL and media use named volumes.

After deployment, check `docker compose ps`, `docker compose logs api worker beat`, `/health`, the frontend, admin login and sitemap. Submit the sitemap in Search Console. Credentials required for optional features are TMDB, the lawful OTT research API, Telegram, Discord, SMTP, Google Analytics/Search Console and AdSense.
