# Indian OTT Tracker

Indian OTT Tracker is a movie-only discovery application for Indian cinema. The V1 catalogue preserves the existing approximately 12,281 movies; TV and YouTube are deliberately deferred. FastAPI and PostgreSQL provide discovery, rich movie/person data, evidence-first India OTT intelligence and protected operations. React/Vite supplies the public and administrative interfaces. Redis, Celery workers and Celery Beat run resumable enrichment, image, OTT and health workflows.

## Public experience

The frontend implements home, discover, categorized search, genre, language, OTT landing/platform, movie, person, six calendar periods, movie request and legal routes. Every calendar period has separate theatrical and confirmed canonical OTT tabs. Discovery combines language, genre, year, IMDb rating, certification, release status, platform, normalized people roles and custom dates, with eight sorts. Movie pages render only stored values: artwork galleries, releases, ratings, OTT verification facts, credits with profile images, keywords, production information, collection and external IDs. The public detail page labels the existing TMDB identity generically as `ID`; internal database IDs remain routing/storage identifiers and are never presented as that public ID.

The protected Admin Control Center covers requests and email delivery, catalogue movies, comment moderation, data and image health, the OTT evidence/reconciliation command center, manual and scheduled research history, a 100-movie manual gold set, authorized OTTplay/JustWatch adapter feeds, jobs/backfills, notifications, integration status, service health, and an administrator audit trail. Administrators can preview and confirm an eligible queue, research one request/movie, choose a TMDB/IMDb/OTT/web scope, and inspect the resulting queries, providers, sources, evidence and before/after canonical facts. OTT availability and original OTT dates are separate facts; public exact dates require confirmed evidence. Operational lists use server-side filters and pagination where catalogue size requires them.

Optional India availability sources include TMDB/JustWatch, Streaming Availability, and Watchmode. Each has independent enablement, budgets, caching, health, and failure isolation. The engine records observation history and provider provenance but defaults `OTT_INTELLIGENCE_AUTO_PUBLICATION_ENABLED=false` until the manually verified gold set passes its accuracy gate. See `docs/OTT_RESEARCH.md` before enabling production providers or phased backfill.

IMDb is the primary public rating. Ratings are never copied from TMDB or fabricated. Configure an approved OMDb API account with `IMDB_RATING_PROVIDER=omdb`, `IMDB_RATING_API_URL` and `IMDB_RATING_API_KEY` to enable bounded background refreshes. The stored IMDb external ID is the provider lookup key; the existing `movie_ratings` record stores the returned score, vote count and check time. Admin → System Health reports the exact missing variable names without exposing values and offers a one-known-title connection test.

## Local Windows setup

```powershell
Set-Location C:\Users\anadh\Development\indian-ott-tracker
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

Open `http://localhost:5173`; API docs are at `http://localhost:8000/docs`. To enable optional pgAdmin, run `docker compose --profile tools up -d pgadmin` and open `http://localhost:5050`.

For host-side development:

```powershell
Set-Location C:\Users\anadh\Development\indian-ott-tracker
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
Set-Location .\frontend
npm ci
npm run dev
```

## Validation

```powershell
Set-Location C:\Users\anadh\Development\indian-ott-tracker
.\.venv\Scripts\python.exe -m compileall -q app alembic
.\.venv\Scripts\python.exe -m pytest
Set-Location .\frontend
npm ci
npm test
npm run build
Set-Location ..
docker compose config --quiet
```

API startup runs additive Alembic migrations and named volumes retain PostgreSQL and media data. Never remove those volumes during an update. Configure only credentials/accounts listed in `.env.example`; no admin key or session secret is shipped to browser JavaScript.

See the documents in `docs/` for deployment, schema, operations, OTT evidence policy, images, SEO, AdSense and notifications.
