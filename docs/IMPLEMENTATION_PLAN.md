# Indian OTT Tracker — Implementation Plan

**Created:** 2026-08-26  
**Based on:** `docs/PROJECT_AUDIT.md`  
**Principle:** Incremental, testable phases. Preserve 12,281 existing movies and all IDs.

---

## Guiding Rules

1. **Never delete production movie data**
2. **Movie-only** — no TV features, no YouTube
3. **Additive migrations only** — no destructive schema changes
4. **Extend existing code** before creating parallel systems
5. **Feature branches** for substantial work
6. **Test each phase** before proceeding
7. **Commit uncommitted work** before new schema changes

---

## Phase Overview

| Phase | Name | Duration est. | Depends on |
|-------|------|---------------|------------|
| 0 | Audit & stabilization | 2–3 days | — |
| 1 | Foundation hardening | 2–3 days | Phase 0 |
| 2 | Metadata enrichment at scale | 4–5 days | Phase 1 |
| 3 | Image fallback system | 4–5 days | Phase 2 |
| 4 | Frontend scaffold + movie pages | 5–7 days | Phase 1 |
| 5 | Rich movie detail pages | 4–5 days | Phase 3, 4 |
| 6 | People & credits discovery | 3–4 days | Phase 2, 4 |
| 7 | Search, filter & sort | 4–5 days | Phase 4 |
| 8 | OTT research queue & daily research | 5–7 days | Phase 2 |
| 9 | Data quality & notifications | 4–5 days | Phase 3, 8 |
| 10 | Movie request system | 2–3 days | Phase 1 |
| 11 | SEO & discoverability | 3–4 days | Phase 4, 5, 6 |
| 12 | Google Analytics & Search Console | 1–2 days | Phase 11 |
| 13 | AdSense readiness | 2–3 days | Phase 11 |
| 14 | Legal & trust pages | 1–2 days | Phase 4 |
| 15 | Performance optimization | 3–4 days | Phase 7, 11 |
| 16 | Security hardening | 2–3 days | Phase 10 |
| 17 | Testing & production hardening | 3–5 days | All |

**Total estimate:** 10–14 weeks focused development

---

## Phase 0 — Audit & Stabilization

**Goal:** Understand the system, preserve uncommitted work, establish baseline.

### Tasks

- [x] Run git status, remote, branch verification
- [x] Inspect full repository structure
- [x] Query live database statistics
- [x] Create `docs/PROJECT_AUDIT.md`
- [x] Create `docs/IMPLEMENTATION_PLAN.md`
- [ ] Review uncommitted changes with owner
- [ ] Commit metadata migration + services on feature branch
- [ ] Verify backup `data/backups/ott_tracker_before_rich_metadata_20260821.dump` is restorable

### Exit criteria

- Audit docs complete
- Uncommitted work preserved and committed safely
- Team agrees on movie-only scope and no-TV policy

### Tests

```powershell
git status
git remote -v
alembic current
# DB count query via psql or python
```

---

## Phase 1 — Foundation Hardening

**Goal:** Stable, documented, production-safe backend baseline.

### Tasks

1. **Documentation**
   - Write root `README.md` (setup, Docker, migrations, scripts)
   - Populate `.env.example` (all required vars, no secrets)

2. **Database layer cleanup**
   - Consolidate `connection.py` and `session.py` into single module
   - Update all imports (no behavior change)

3. **Health & config**
   - Enhance `GET /health` — Postgres `SELECT 1`, Redis `PING`
   - Add env-configurable CORS origins
   - Add optional settings: `FANART_API_KEY`, `MEDIA_ROOT`, `CORS_ORIGINS`

4. **Docker improvements**
   - Add postgres/redis healthchecks
   - Document migration procedure (do not auto-run on startup yet)

5. **Reference data**
   - Run OTT platform seed (`scripts/seed_platforms.py` or `app/services/ott/seed_platforms.py`)
   - Verify genres/languages are complete

6. **API structure**
   - Wire `app/api/v1/router.py` (move existing movie routes under `/api/v1`)
   - Keep backward-compatible redirects or dual-mount temporarily

### Files likely touched

- `README.md`, `.env.example`, `app/main.py`, `app/config/settings.py`
- `app/database/connection.py` or `session.py` (consolidate)
- `docker-compose.yml` (healthchecks only)
- `app/api/v1/router.py`

### Exit criteria

- `docker compose up` → healthy API with DB/Redis checks
- README allows new developer setup
- OTT platforms seeded
- No regression in existing movie API

### Tests

```powershell
docker compose up -d
curl http://localhost:8000/health
curl "http://localhost:8000/movies/?page=1&page_size=5"
pytest tests/ -v
```

---

## Phase 2 — Metadata Enrichment at Scale

**Goal:** Enrich all 12,281 existing movies with TMDB metadata without changing IDs.

### Tasks

1. **Batch enrichment job**
   - Extend `scripts/enrich_movie_metadata.py` with batch mode, rate limiting, checkpoint
   - Process movies where `people`/`movie_images`/`external_ids` are missing
   - Idempotent: use existing `MovieMetadataService._set_if_present` pattern

2. **TMDB gaps**
   - Add alternative titles table + ingestion
   - Add collection/belongs-to-collection fields
   - Add recommendations/similar (store TMDB IDs, lazy fetch)

3. **Background scheduling**
   - Add Celery worker + beat OR APScheduler container to docker-compose
   - Daily incremental TMDB sync job

4. **Logging**
   - Replace print statements in TMDB client with structured loguru logging

### Files likely touched

- `app/services/movie_metadata_service.py`
- `app/services/tmdb/movie_service.py`
- `scripts/enrich_movie_metadata.py`
- `app/workers/tmdb_worker.py`
- `docker-compose.yml` (worker service)
- New migration for alternative titles / collection (additive)

### Exit criteria

- >80% of movies have external IDs and basic credits
- Enrichment job is resumable and idempotent
- Daily sync runs in Docker without manual intervention

### Tests

```powershell
python scripts/enrich_movie_metadata.py --limit 10
curl http://localhost:8000/movies/1/cast
curl http://localhost:8000/movies/1/external-ids
pytest tests/test_bulk_importer.py -v
```

---

## Phase 3 — Image Fallback System

**Goal:** Configurable multi-source image recovery with health monitoring.

### Tasks

1. **ImageFallbackService**
   - Unify `PosterService`, `ArtworkService` under single service
   - Configurable provider chain via settings (TMDB → Fanart → future providers)
   - Record source, URL, failure reason on `MovieImage`

2. **Person image support**
   - Extend fallback to `Person.profile_path`
   - Cache locally under `/media/people/`

3. **Placeholder system**
   - Generic placeholder images by type (poster, backdrop, logo, person)

4. **Image health job**
   - Detect missing/broken URLs
   - Retry with fallback chain
   - Mark unresolved after all sources fail

5. **Migration (additive)**
   - Add columns: `failure_reason`, `health_status`, `last_checked_at` to `movie_images`
   - Or create `image_health_log` table

### Exit criteria

- Primary poster/backdrop available for majority of enriched movies
- Broken images detected and retried automatically
- No hard-coded providers in frontend (backend resolves URLs)

### Tests

- Unit tests for fallback chain ordering
- Integration test: movie with missing poster triggers recovery

---

## Phase 4 — Frontend Scaffold + Core Pages

**Goal:** Create React frontend (Vite + TypeScript recommended) consuming existing API.

### Tasks

1. **Scaffold frontend/**
   - Vite + React + TypeScript
   - React Router, TanStack Query for API
   - Tailwind CSS or existing preference
   - Mobile-first layout shell (header, footer, nav)

2. **Core pages (initial)**
   - Home (placeholder sections)
   - Movie listing page
   - Movie detail page (basic — expand in Phase 5)
   - 404 / error / empty states
   - Skeleton loaders

3. **Docker**
   - Add `frontend` service to docker-compose
   - Proxy API calls to backend

4. **API client**
   - Typed client for `/movies` endpoints
   - Environment-based API base URL

### Exit criteria

- Frontend loads at `localhost:5173`
- Movie list and basic detail page work
- Responsive on mobile/tablet/desktop

### Tests

```powershell
cd frontend
npm run build
npm run test
```

---

## Phase 5 — Rich Movie Detail Pages

**Goal:** Plex-like movie detail depth using existing sub-APIs.

### Tasks

1. **Detail page sections** (hide empty sections)
   - Header: poster, backdrop, logo, title, tagline, overview
   - Metadata: genres, languages, countries, companies, keywords
   - Identifiers: TMDB, IMDb, etc.
   - Release information with country-specific dates
   - OTT availability block
   - Cast grid + crew grouped by department
   - Media gallery (posters, backdrops, logos)

2. **Backend enhancements**
   - Single aggregated endpoint `GET /movies/{id}/detail` (optional — reduces round trips)
   - Or frontend parallel fetch of sub-resources

3. **Responsive images**
   - Lazy loading, srcset where applicable
   - Serve from `/media` with fallback placeholder

### Exit criteria

- Detail page shows all available data from API
- Empty sections hidden
- No fabricated data displayed

---

## Phase 6 — People & Credits Discovery

**Goal:** Discover movies by actor, director, cinematographer, etc.

### Tasks

1. **Backend APIs**
   - `GET /people` — search/list
   - `GET /people/{id}` — profile
   - `GET /people/{id}/movies` — filmography with role info

2. **Frontend pages**
   - Person profile page
   - Filmography list with role badges
   - Clickable cast/crew on movie detail → person page

3. **Deduplication**
   - Enforce unique `people.tmdb_id`
   - Merge strategy documented (never duplicate by TMDB ID)

### Exit criteria

- Click actor on movie page → see filmography
- Search by person name returns results

---

## Phase 7 — Search, Filter & Sort

**Goal:** Powerful movie discovery without loading entire DB client-side.

### Tasks

1. **Backend**
   - PostgreSQL full-text search (movies.title, original_title, alternative titles)
   - People/keyword join search
   - Extended filters: OTT platform, certification, rating range, release status
   - Extended sort: OTT release date, upcoming, recently added
   - Date range filters with timezone awareness (Asia/Kolkata default)
   - Return pagination metadata (`PaginationMeta` schema exists)

2. **Frontend**
   - Search bar (global)
   - Filter panel (mobile drawer)
   - Sort dropdown
   - Date range picker

3. **Caching**
   - Redis cache for popular queries (homepage sections, trending)

### Exit criteria

- Search by title, actor, director works
- Filters apply server-side with pagination
- No full DB load in browser

---

## Phase 8 — OTT Research Queue & Daily Research

**Goal:** Compliant, evidence-based OTT research replacing blind scraping.

### Tasks

1. **Schema (additive migration)**
   - `ott_research_queue` table per spec
   - `ott_research_sources` evidence table
   - Status enum: UNKNOWN, RESEARCHING, POSSIBLE, CONFIRMED, CONFLICTING, NOT_FOUND, NEEDS_REVIEW

2. **Queue population**
   - After TMDB sync, enqueue movies missing OTT platform/date

3. **Research engine**
   - Configurable providers (TMDB watch providers as primary)
   - Manual/admin-confirmed sources
   - Confidence scoring and evidence storage
   - Never overwrite CONFIRMED with low-confidence data

4. **Daily workflow**
   - Scheduled job: queue → research → evaluate → update
   - Rate limiting and idempotency

5. **Deprecate/replace**
   - Gate `GoogleSearchOttService` behind research queue with review threshold
   - Remove or disable raw search scraping if ToS-noncompliant

6. **API**
   - `GET /ott-research` (admin-only initially)
   - `POST /movies/{id}/sync-ott` delegates to queue

### Exit criteria

- Movies missing OTT info enter queue automatically
- Research results stored with evidence and confidence
- Conflicting sources flagged, not silently merged

---

## Phase 9 — Data Quality & Notifications

**Goal:** Monitor data health and alert administrator.

### Tasks

1. **Data quality checks**
   - Missing poster, backdrop, OTT date, IMDb ID, cast, director, etc.
   - Broken images, duplicate candidates, invalid dates
   - Store issues in `data_quality_issues` table

2. **API & page**
   - `GET /data-health` — real DB counts
   - Admin dashboard view (protected)

3. **Notifications**
   - Telegram and/or Discord via env-configured webhooks
   - Deduplication, cooldown, severity levels
   - Daily summary notification

### Exit criteria

- `/data-health` returns actual counts
- Admin receives notification for new unresolved issues (not repeated spam)

---

## Phase 10 — Movie Request System

**Goal:** Allow users to request missing movies.

### Tasks

1. **Schema:** `movie_requests` table with statuses
2. **API:** `POST /requests`, admin review endpoints
3. **Frontend:** `/request-movie` page, opens in **new tab**, form validation, rate limiting
4. **Notifications:** Admin alert on new request
5. **Security:** Sanitize input, never expose requester email publicly

### Exit criteria

- User can submit request with email
- Admin can review and update status

---

## Phase 11 — SEO & Discoverability

**Goal:** Production-grade search engine optimization.

### Tasks

1. SEO-friendly URLs: `/movies/{slug}-{id}` or `/movie/{slug}`
2. Unique title/meta per page (React Helmet or SSR meta)
3. Open Graph + Twitter cards
4. JSON-LD: Movie, Person, WebSite, BreadcrumbList
5. XML sitemap (paginated generation for 12k+ movies)
6. `robots.txt`
7. Canonical URLs
8. Avoid indexing infinite filter combinations

### Documentation

- Create `docs/SEO.md`

### Exit criteria

- Sitemap accessible at `/sitemap.xml`
- Movie pages have unique meta and structured data
- Google Rich Results Test passes for sample pages

---

## Phase 12 — Google Analytics & Search Console

**Goal:** Technical readiness for Google integrations.

### Tasks

1. GA4 integration via env `VITE_GA_MEASUREMENT_ID`
2. Search Console verification meta tag (env-configurable)
3. Document setup in `docs/DEPLOYMENT.md`

### Exit criteria

- GA loads only when measurement ID configured
- Verification tag can be added without code change

---

## Phase 13 — AdSense Readiness

**Goal:** Technical preparation (not approval guarantee).

### Tasks

1. Ad container components (configurable, non-intrusive)
2. `ads.txt` route serving from env publisher ID
3. Consent/privacy banner hook
4. Document in `docs/ADSENSE.md`

### Exit criteria

- `/ads.txt` serves configured publisher line
- Ad slots render placeholder when no publisher ID

---

## Phase 14 — Legal & Trust Pages

**Goal:** Required trust pages for users and ad/analytics compliance.

### Tasks

1. Pages: `/about`, `/contact`, `/privacy`, `/terms`, `/cookies`
2. Footer links on all pages
3. Configurable business/contact details via env
4. Accurate statements about analytics, cookies, third-party services

### Exit criteria

- All five pages accessible and linked from footer

---

## Phase 15 — Performance Optimization

**Goal:** Core Web Vitals, fast mobile experience.

### Tasks

1. Measure baseline (Lighthouse)
2. Code splitting, lazy routes
3. Redis caching for homepage sections and search
4. Database index review for filter/sort queries
5. Image optimization (WebP where supported)
6. API response compression

### Exit criteria

- Lighthouse mobile performance > 80
- Movie list loads < 2s on 4G

---

## Phase 16 — Security Hardening

**Goal:** Production-safe deployment.

### Tasks

1. Admin auth (API key or JWT for admin endpoints)
2. Rate limiting on public endpoints (especially `/requests`)
3. Security headers middleware
4. CORS lockdown for production
5. Input validation audit
6. Dependency vulnerability scan
7. Hide stack traces in production

### Exit criteria

- Admin endpoints require authentication
- Public endpoints rate-limited
- No secrets in git

---

## Phase 17 — Testing & Production Hardening

**Goal:** Confidence to deploy.

### Tasks

1. Proper pytest suite (replace script-style tests)
2. Frontend component tests
3. API integration tests against Postgres
4. Migration up/down tests
5. CI pipeline (GitHub Actions)
6. Production deployment documentation
7. Final `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md`, `docs/DATABASE.md`

### Exit criteria

- CI runs on every PR
- All critical paths tested
- Deployment guide complete

---

## Immediate Next Steps (Post-Audit)

### Step 1 — Preserve uncommitted work

Create feature branch and commit:

```
feature/rich-movie-metadata
```

Files to include:
- Modified API, models, services, docker-compose
- New migration, metadata models, artwork service, enrichment script

### Step 2 — Begin Phase 1 (Foundation Hardening)

After owner approval:
1. README + `.env.example`
2. Health check enhancement
3. Seed OTT platforms
4. Consolidate DB session modules

### Step 3 — Do NOT start yet

- Frontend (Phase 4) until Phase 1 complete
- OTT research queue (Phase 8) until metadata enrichment underway
- Any destructive migration

---

## Documentation Deliverables

| Document | Phase | Status |
|----------|-------|--------|
| `docs/PROJECT_AUDIT.md` | 0 | ✓ Created |
| `docs/IMPLEMENTATION_PLAN.md` | 0 | ✓ Created |
| `docs/ARCHITECTURE.md` | 17 | Pending |
| `docs/DATABASE.md` | 17 | Pending |
| `docs/OTT_RESEARCH.md` | 8 | Pending |
| `docs/IMAGE_FALLBACK.md` | 3 | Pending |
| `docs/SEO.md` | 11 | Pending |
| `docs/ADSENSE.md` | 13 | Pending |
| `docs/DEPLOYMENT.md` | 17 | Pending |

---

## Deferred Features (Do Not Implement Now)

- YouTube / trailers / teasers
- TV shows
- User accounts / watchlists
- Plex integration
- AI recommendations
- n8n integration (unless discovered in audit — none found)

---

*This plan will be updated as phases complete and new information emerges from implementation.*
