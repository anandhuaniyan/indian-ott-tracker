# Remaining V1 checklist

This file records implemented behavior, not scaffolding. TV and YouTube remain deferred by scope.

- [x] Genre and language pages with filtering, sorting and pagination
- [x] OTT landing/platform pages using canonical availability
- [x] Full backend-driven home sections
- [x] Rich movie detail, galleries, releases, ratings, credits and identifiers
- [x] Person filmography ordering, cast/crew and normalized role controls
- [x] Categorized movie/people search and combined Discover filters/sorts/dates
- [x] Poster, backdrop, logo and profile validation/recovery with persistent cursors
- [x] OTT states, source ranking, conflict policy, canonical publishing and retries
- [x] Whole-catalogue deduplicating data-health cursor scan
- [x] Celery worker/Beat schedules, job state, notification and cleanup hooks
- [x] Admin dashboard, requests, data health, images, OTT, jobs and notifications
- [x] Signed admin auth, rate limits, same-origin mutations and production cookie flags
- [x] Telegram, Discord and SMTP isolation, persistence and dedupe
- [x] Route SEO, JSON-LD, escaped complete sitemap and Search Console support
- [x] Consent-gated analytics and AdSense loader/slot
- [x] Separate substantive legal pages
- [x] Route splitting, API caching, responsive lazy images and database indexes
- [x] Docker topology/health checks and complete `.env.example`
- [x] README and implementation-specific documentation
- [x] Backend and frontend automated tests

Validation snapshot: Python compile/import succeeded; Pytest 16 passed; frontend Vitest 10 passed; Vite production build succeeded; npm audit found 0 vulnerabilities; Compose configuration parsed; PostgreSQL/API/Redis/frontend/worker/Beat health checks passed; the additive migration retained exactly 12,281 movies. No unchecked V1 code item remains.
