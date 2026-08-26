# Architecture

The FastAPI application is the source of truth for discovery, requests, operational data and the existing TMDB ingestion pipeline. PostgreSQL stores all persistent data and Redis remains the shared background-job broker/cache. The React/Vite single-page frontend is deliberately read-only apart from the public request form; it never downloads the movie catalogue to filter locally.

`/api/v1/discover` applies query, people, language, genre, year, platform and date filters at the database. `/api/v1/home` supplies dynamic home rails. `/api/v1/people/{id}` returns role-aware filmography. Mutating enrichment and OTT actions require `X-Admin-Key`, as do operational views.

The additive `d4e5f6a7b8c9` migration leaves movie records and legacy TV tables untouched. It adds request, OTT-evidence, health-issue and notification-log storage.
