# Indian OTT Tracker — Project Audit

**Audit date:** 2026-08-26  
**Workspace:** `C:\Users\anadh\Development\indian-ott-tracker`  
**Remote:** `https://github.com/anandhuaniyan/indian-ott-tracker.git`  
**Branch:** `main` (up to date with origin)

---

## 1. Git & Working Tree Status

### Verified Git configuration

| Check | Result |
|-------|--------|
| Remote origin | `https://github.com/anandhuaniyan/indian-ott-tracker.git` ✓ |
| Current branch | `main` |
| Tracking | Up to date with `origin/main` |

### Uncommitted changes (preserved — not modified during audit)

**Modified (not staged):**

| File | Nature of change |
|------|------------------|
| `app/api/movies.py` | Rich metadata endpoints (cast, crew, images, releases, ratings, external IDs, enrich, sync-ott) |
| `app/config/settings.py` | Added `MEDIA_ROOT`, pydantic v2 `SettingsConfigDict` |
| `app/main.py` | CORS, static `/media` mount, movie router |
| `app/models/__init__.py` | Exports rich metadata models + dual OTT models |
| `app/models/movie.py` | Rich metadata relationships, `OttAvailability` integration |
| `app/services/tmdb/movie_service.py` | `get_rich_movie_details()` for enrichment |
| `docker-compose.yml` | Added `ott_media_data` volume for `/app/media` |

**Untracked (preserved):**

| Path | Purpose |
|------|---------|
| `alembic/versions/c3d4e5f6a7b8_add_rich_movie_metadata.py` | Additive migration for people, credits, images, etc. |
| `app/models/movie_metadata.py` | Normalized metadata ORM models |
| `app/services/artwork_service.py` | Local artwork caching from TMDB URLs |
| `app/services/movie_metadata_service.py` | Idempotent TMDB enrichment for existing movies |
| `scripts/enrich_movie_metadata.py` | CLI to enrich movies |
| `media/` | Cached poster/backdrop/logo files |
| `data/backups/ott_tracker_before_rich_metadata_20260821.dump` | Pre-migration DB backup |
| `PROJECT_STATUS_20260821_215500.txt` | Environment snapshot |
| `ott-sync-diagnostic.ps1` | Diagnostic script |

**Alembic head in database:** `c3d4e5f6a7b8` (uncommitted migration already applied locally)

---

## 2. Current Architecture

### High-level system diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Client Layer                             │
│  (No frontend in repo — API-only today)                      │
│  CORS pre-configured for localhost:5173, localhost:8081      │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼──────────────────────────────────┐
│  FastAPI (app/main.py)                                       │
│  ├── GET /, /health                                          │
│  ├── /media/* (StaticFiles — local artwork cache)            │
│  └── /movies/* (app/api/movies.py)                           │
└──────────┬───────────────────────────────┬────────────────────┘
           │                               │
┌──────────▼──────────┐         ┌──────────▼──────────┐
│  Service Layer       │         │  Background Scripts  │
│  - MovieService      │         │  - tmdb_worker.py    │
│  - MovieMetadataSvc  │         │  - sync_ott_job.py   │
│  - OttAvailabilitySvc│         │  - bulk_import.py    │
│  - TMDbClient        │         │  - enrich_metadata   │
│  - ArtworkService    │         └─────────────────────┘
│  - PosterService     │
│  - GoogleSearchOtt   │
└──────────┬───────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│  Repository Layer                                            │
│  MovieRepository, GenreRepository, LanguageRepository,       │
│  OttAvailabilityRepository                                   │
└──────────┬──────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│  PostgreSQL 16 (12,281 movies)                               │
│  Redis 7 (running, unused in application code)               │
└─────────────────────────────────────────────────────────────┘
```

### Docker Compose services

| Service | Image | Host port | Status |
|---------|-------|-----------|--------|
| `postgres` | postgres:16 | 5433 | Running |
| `pgadmin` | dpage/pgadmin4 | 5050 | Configured |
| `redis` | redis:7-alpine | 6380 | Running, **unused** |
| `api` | Custom Python 3.12 | 8000 | Configured with hot-reload |

**Missing from compose:** frontend container, Celery worker, Celery beat, scheduler service, dedicated migration runner.

---

## 3. Frontend Structure

### Current state: **Not implemented**

| Expected (per project brief) | Actual |
|------------------------------|--------|
| React frontend | **No `frontend/` directory** |
| `package.json` | **Missing** (only empty `package-lock.json` exists) |
| Pages, routing, components | **None** |
| SEO meta tags, sitemap | **None** |
| Mobile-first UI | **None** |

### Backend readiness for frontend

- CORS allows `http://localhost:5173` (Vite) and `http://localhost:8081` (Expo/React Native web)
- `/movies` API returns paginated, filterable movie lists
- `/movies/{id}` returns movie with OTT summary
- Sub-resource endpoints exist for cast, crew, images, releases, ratings, external IDs
- Static media served at `/media/*`

**Conclusion:** The project is currently a **backend API with scripts**. The entire user-facing website must be built.

---

## 4. Backend Structure

### FastAPI layout

```
app/
├── main.py                 # App entry, CORS, media mount, router include
├── api/
│   └── movies.py           # Only active API module (no v1 router wired)
├── config/settings.py      # Env-based settings
├── database/
│   ├── base.py             # SQLAlchemy DeclarativeBase
│   ├── connection.py       # Engine + get_db() dependency
│   └── session.py          # Duplicate engine + SessionLocal
├── models/                 # ORM models (see Database section)
├── schemas/                # Pydantic v2 schemas
├── repositories/           # Data access layer
├── services/
│   ├── movie_service.py
│   ├── movie_metadata_service.py
│   ├── ott_availability_service.py
│   ├── artwork_service.py
│   ├── google_search_service.py
│   ├── posters/            # TMDB → Fanart fallback chain
│   ├── ott/
│   └── tmdb/               # Client, sync, bulk import, OTT parsing
├── scheduler/sync_ott_job.py
└── workers/tmdb_worker.py
```

### Active API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Project status JSON |
| GET | `/health` | Basic health (no DB/Redis check) |
| GET | `/movies/` | Paginated list with language, genre, year, sort filters |
| GET | `/movies/search/` | Title search |
| GET | `/movies/{id}` | Movie detail with OTT summary |
| GET | `/movies/{id}/cast` | Cast credits |
| GET | `/movies/{id}/crew` | Crew credits |
| GET | `/movies/{id}/images` | Image records with local/remote URLs |
| GET | `/movies/{id}/releases` | Release dates by country/type |
| GET | `/movies/{id}/ratings` | Ratings by source |
| GET | `/movies/{id}/external-ids` | IMDb, Wikidata, social IDs |
| POST | `/movies/{id}/enrich` | Trigger TMDB metadata enrichment |
| POST | `/movies/{id}/sync-ott` | Trigger OTT availability sync |

### Missing API endpoints (required by spec)

| Endpoint area | Status |
|---------------|--------|
| `/people`, `/people/{id}`, `/people/{id}/movies` | Missing |
| `/genres`, `/languages`, `/ott-platforms` | Missing |
| `/search` (global) | Partial — only `/movies/search/` |
| `/data-health` | Missing |
| `/ott-research` | Missing |
| `/requests` (movie request form) | Missing |
| Homepage section feeds | Missing |
| Sitemap, robots.txt | Missing |
| Admin APIs | Missing |

### Authentication

- **Not implemented.** `SECRET_KEY` exists in settings but is unused.
- All endpoints are public.
- Admin functionality has no auth gate.

### Error handling & logging

- Basic FastAPI HTTPException for 404
- TMDB client uses print-based logging with retry/backoff
- `loguru` in requirements but not wired centrally
- No structured logging middleware
- No global exception handler hiding stack traces

---

## 5. Database Structure

### Live database statistics (2026-08-26)

| Table | Row count |
|-------|-----------|
| `movies` | **12,281** |
| `people` | 61 |
| `movie_images` | 49 |
| `ott_availability` | 1 |
| `genres` | 19 |
| `languages` | 187 |
| `ott_platforms` | 0 |

**Critical:** 12,281 movies must be preserved. IDs and TMDB IDs are production data.

### Migration history

| Revision | Description |
|----------|-------------|
| `18ee4436bfb5` | Initial schema (movies, genres, languages, TV tables, legacy OTT) |
| `932a0a90eb34` | Empty pass migration |
| `a1b2c3d4e5f6` | OTT availability table |
| `b2c3d4e5f6a7` | Add `status` to movies |
| `c3d4e5f6a7b8` | Rich metadata tables (people, credits, images, etc.) — **uncommitted, applied locally** |

### Core tables

| Table | Model file | Notes |
|-------|-----------|-------|
| `movies` | `movie.py` | Primary entity; 12,281 records |
| `genres` | `genre.py` | M2M via `movie_genres` |
| `languages` | `language.py` | M2M via `movie_languages` |
| `people` | `movie_metadata.py` | TMDB person records |
| `movie_credits` | `movie_metadata.py` | Cast + crew |
| `keywords` | `movie_metadata.py` | TMDB keywords |
| `production_companies` | `movie_metadata.py` | TMDB companies |
| `production_countries` | `movie_metadata.py` | Origin countries |
| `external_ids` | `movie_metadata.py` | IMDb, Wikidata, social |
| `movie_release_dates` | `movie_metadata.py` | Theatrical/digital/certification |
| `movie_images` | `movie_metadata.py` | Posters, backdrops, logos |
| `movie_ratings` | `movie_metadata.py` | Multi-source ratings |
| `ott_availability` | `ott_availability.py` | **Active** OTT tracking |
| `ott_platforms` | `ott_platform.py` | Seeded table — **0 rows** |
| `movie_ott_availability` | `movie_ott.py` | **Legacy** — FK to ott_platforms |

### TV show tables (exist but must NOT be used)

| Table | Model | Policy |
|-------|-------|--------|
| `tv_shows` | `tv_show.py` | **Do not implement TV features** |
| `tv_show_ott_availability` | `tv_show_ott.py` | **Do not implement TV features** |
| `tv_show_genres`, `tv_show_languages` | association tables | Legacy scaffold |

Per project rules: website is **movie-only**. TV models should remain dormant, not deleted (avoid destructive migration).

### Duplicate / overlapping systems

| Area | Duplicate | Recommendation |
|------|-----------|----------------|
| OTT tracking | `OttAvailability` vs `MovieOtt` | Use `OttAvailability` exclusively; keep `MovieOtt` table for compatibility until safe deprecation |
| DB sessions | `connection.py` vs `session.py` | Consolidate to one module |
| Poster/images | `PosterService` (posters/) vs `ArtworkService` + `MovieImage` | Unify under `ImageFallbackService` per spec |
| OTT sync | `sync_ott.py`, `ott_service.py`, `OttAvailabilityService`, `GoogleSearchOttService` | Consolidate behind research queue in later phase |
| API routing | `app/api/movies.py` vs empty `app/api/v1/` | Wire v1 router when frontend is built |

### Missing tables (required by spec)

| Concept | Status |
|---------|--------|
| `ott_research_queue` | Missing |
| `ott_research_sources` | Missing |
| `movie_requests` | Missing |
| `data_quality_issues` | Missing |
| `image_sources` / image health tracking | Partial — `MovieImage` has source fields but no health job |
| `notification_log` | Missing |
| Full-text search indexes | Missing |

### Indexes

- Basic indexes on FK columns and unique constraints exist
- **Missing:** PostgreSQL full-text search indexes for title/alternative titles/people
- **Missing:** Composite indexes for common filter combinations (language + year + sort)

---

## 6. TMDB Implementation

### What exists

| Component | File | Status |
|-----------|------|--------|
| HTTP client | `tmdb/client.py` | Rate limiting (0.25s delay), retry with backoff, 429 handling |
| Movie API wrapper | `tmdb/movie_service.py` | get, search, discover, rich details |
| Bulk importer | `tmdb/bulk_importer.py` | Checkpointed import by language/year; supports ml, ta, te, hi, kn |
| Incremental sync | `tmdb/incremental_sync.py` | Latest movie sync |
| Sync movies | `tmdb/sync_movies.py` | Language/year discovery sync |
| Sync genres/languages | `sync_genres.py`, `sync_languages.py` | Reference data sync |
| Metadata enrichment | `movie_metadata_service.py` | Idempotent upsert of credits, images, releases, external IDs |
| Worker entry | `workers/tmdb_worker.py` | Daily sync + bulk import CLI |

### Data collected from TMDB

- Basic movie fields (title, overview, dates, runtime, posters, ratings)
- Watch providers (via `tmdb/ott_service.py`)
- Rich metadata when enrichment runs: credits, external IDs, keywords, production, release dates, images, ratings

### Gaps vs spec

| Requirement | Status |
|-------------|--------|
| Alternative titles | **Missing** |
| Collection / belongs-to-collection | **Missing** |
| Recommendations / similar movies | **Missing** |
| Certifications (partial via release_dates) | Partial |
| Idempotent ingestion | ✓ (upsert by tmdb_id) |
| Retryable | ✓ (client level) |
| Rate-limit aware | ✓ |
| Structured logging | Partial (print statements) |
| Scheduled daily sync in Docker | **Not wired** — manual script only |
| Enrichment at scale | Only 61 people, 49 images for 12,281 movies |

### TMDB API key

- Stored in `.env` (gitignored) ✓
- Not in committed code ✓
- `.env.example` is **empty** — onboarding gap

---

## 7. OTT Implementation

### What exists

| Component | Description |
|-----------|-------------|
| `OttAvailability` model | Provider, country, watch_type, release date, confidence, source_type, source_url |
| `OttAvailabilityService` | TMDB providers first, Google Search fallback if confidence ≥ 90% |
| `GoogleSearchOttService` | Scrapes DuckDuckGo HTML results, parses platform/date |
| `sync_ott_job.py` | Daily batch sync script (not scheduled in Docker) |
| API | `POST /movies/{id}/sync-ott` |

### Gaps vs spec

| Requirement | Status |
|-------------|--------|
| OTT Research Queue | **Missing** |
| Research statuses (UNKNOWN, CONFIRMED, CONFLICTING, etc.) | **Missing** |
| Evidence storage | Partial (source_url on OttAvailability) |
| Confidence-based update rules | Partial (Google ≥ 90% threshold) |
| Never overwrite confirmed with low-confidence | **Not implemented** |
| Configurable research providers | **Missing** |
| Daily scheduled workflow in Docker | **Missing** |
| `ott_platforms` seed data | **0 rows** — seed script exists but not run |
| Official source prioritization | Partial in GoogleSearchOttService domain list |

### Risk: Google Search fallback

The current `GoogleSearchOttService` performs HTML scraping of search results. This may conflict with:
- Project rule: "Do not build mechanisms intended to circumvent search-engine protections"
- Terms of service for search providers

**Recommendation:** Replace with configurable, ToS-compliant research providers (official APIs, RSS, manual review queue) in Phase 7.

---

## 8. Image Implementation

### What exists

| Component | Description |
|-----------|-------------|
| `MovieImage` model | type, source, original_url, local_path, dimensions, is_primary |
| `ArtworkService` | Downloads primary images from TMDB URLs to `/media` |
| `PosterService` | TMDB → Fanart.tv fallback chain |
| `FanartService` | Optional Fanart.tv API (requires `FANART_API_KEY` env) |
| Static serving | `/media` mounted in FastAPI |
| Docker volume | `ott_media_data:/app/media` |

### Gaps vs spec

| Requirement | Status |
|-------------|--------|
| Multi-source fallback chain (3+ providers) | Partial (TMDB + Fanart only) |
| Configurable backup providers | **Missing** |
| Broken URL detection | **Missing** |
| Image health background job | **Missing** |
| Record failure reason | **Missing** |
| Person/profile image fallback | **Missing** |
| Placeholder system | **Missing** |
| Data-quality issue on unresolved images | **Missing** |
| Admin notification on image failures | **Missing** |

### Coverage

- 49 `movie_images` records for 12,281 movies ≈ **0.4%** image enrichment coverage
- Most movies still rely on TMDB `poster_path` / `backdrop_path` URL strings on the movie row

---

## 9. Docker Architecture

### Strengths

- Postgres, Redis, pgAdmin, API all networked
- Media volume separated from bind mount
- Environment via `.env` file
- Hot-reload for development

### Weaknesses

| Issue | Impact |
|-------|--------|
| No health checks on postgres/redis | API may start before DB ready |
| No Celery/scheduler containers | Background jobs require manual execution |
| No frontend service | Expected per project brief |
| Bind mount `.:/app` includes `.venv` | Cross-platform path issues possible |
| No migration on startup | Migrations must be run manually |
| Dev credentials in compose | Must not reach production unchanged |

---

## 10. Redis Usage

| Expected use | Actual |
|--------------|--------|
| API caching | **Not used** |
| Search caching | **Not used** |
| Background job coordination | **Not used** |
| Rate limiting | **Not used** |
| Celery broker | **Not configured** |

Redis container runs but application code has **zero Redis imports**.

---

## 11. Automation & Background Jobs

| Job | Implementation | Scheduled |
|-----|---------------|-----------|
| TMDB daily sync | `workers/tmdb_worker.py` | Manual |
| TMDB bulk import | `scripts/bulk_import.py` | Manual |
| Metadata enrichment | `scripts/enrich_movie_metadata.py` | Manual |
| OTT daily sync | `scheduler/sync_ott_job.py` | Manual |
| Image health | **Missing** | — |
| OTT research | **Missing** | — |
| Data quality checks | **Missing** | — |

Celery and APScheduler are in `requirements.txt` but not implemented.

---

## 12. Notifications

| Channel | Status |
|---------|--------|
| Telegram | Empty placeholder files from initial scaffold |
| Discord | Empty placeholder files |
| Email | Not implemented |
| Deduplication/cooldown | Not implemented |

---

## 13. Testing

| Test file | Type | Notes |
|-----------|------|-------|
| `tests/test_ott_availability_system.py` | Script-style | Manual assertions, hardcoded Windows path |
| `tests/test_db_and_api.py` | Script-style | SQLite in-memory, hardcoded path |
| `tests/test_bulk_importer.py` | Exists | Not reviewed in detail |
| pytest configuration | **Missing** | pytest installed but no `pytest.ini`, no CI |
| Frontend tests | **Missing** | No frontend |
| Integration tests against Postgres | **Missing** | |

---

## 14. Documentation

| Document | Status |
|----------|--------|
| `README.md` | **Missing from repo root** |
| `.env.example` | **Empty** |
| `docs/` | **Empty** (this audit creates first docs) |
| `.cursor/context.md` | Basic MVP vision (outdated vs current scope) |
| `PROJECT_STATUS_20260821_215500.txt` | Large environment dump (untracked) |

---

## 15. Existing Functionality Summary

### Working today

1. **Docker infrastructure** — Postgres, Redis, pgAdmin, API
2. **12,281 Indian-language movies** imported from TMDB
3. **Movie listing API** with pagination, language/genre/year filters, sort
4. **Movie search API** by title
5. **Movie detail API** with OTT availability summary
6. **Rich metadata sub-APIs** — cast, crew, images, releases, ratings, external IDs
7. **TMDB ingestion pipeline** — bulk import with checkpointing, incremental sync
8. **Metadata enrichment service** — idempotent TMDB upsert for existing movies
9. **OTT availability sync** — TMDB providers + Google fallback
10. **Local artwork caching** — partial coverage
11. **Alembic migrations** — 5 revisions, additive approach

### Not working / not built

1. **Frontend website** (entire UI)
2. **People discovery pages/API**
3. **Global search** (actors, directors, keywords)
4. **Homepage dynamic sections**
5. **Advanced sorting/filtering** (OTT, certification, date ranges)
6. **OTT research queue** with evidence and confidence workflow
7. **Data quality monitoring** (`/data-health`)
8. **Image fallback/recovery system** (spec-compliant)
9. **Movie request system**
10. **SEO** (sitemap, meta, JSON-LD, robots.txt)
11. **Google Analytics / Search Console / AdSense readiness**
12. **Legal/trust pages**
13. **Admin dashboard/APIs**
14. **Notifications**
15. **Redis caching**
16. **Scheduled jobs in Docker**
17. **Production health checks**
18. **Proper pytest suite + CI**

---

## 16. Technical Debt

### High priority

1. **No frontend** — largest gap vs project goal
2. **Uncommitted migration + metadata work** — must be committed safely before further schema changes
3. **Duplicate OTT models** — `OttAvailability` vs `MovieOtt`
4. **Duplicate DB session modules** — `connection.py` vs `session.py`
5. **Redis provisioned but unused**
6. **Google search scraping** — ToS/compliance risk
7. **12,281 movies with ~0.4% image enrichment** — metadata gap
8. **Only 1 OTT availability record** — OTT feature essentially unpopulated
9. **Tests use hardcoded Windows paths** — not portable
10. **Empty `.env.example` and no README**

### Medium priority

11. TV show models in schema (dormant but confusing)
12. Empty scaffold files from Phase 1 (`app/api/v1/`, notifications, celery_worker, etc.)
13. Print-based logging instead of structured logging
14. `/health` doesn't verify DB/Redis
15. No pagination metadata in list API response (schema exists but unused)
16. `932a0a90eb34` empty migration in history
17. Fanart API key not in Settings class

### Low priority

18. `package-lock.json` without `package.json`
19. Stray files: `apprepositoriesgenre_repository.py`, duplicate dump files
20. CORS hardcoded origins (should be env-configurable)

---

## 17. Potential Breaking Changes

| Change | Risk | Mitigation |
|--------|------|------------|
| Dropping TV tables | Migration failure, FK references | **Do not drop** — leave dormant |
| Dropping `movie_ott_availability` | Data loss if legacy data exists | Audit table row count first; migrate if needed |
| Changing movie ID strategy | Breaks all FK relationships | **Never change** — preserve IDs |
| Re-running bulk import | Overwrites metadata | Enrichment uses `_set_if_present` — safe; verify sync_movies doesn't null out fields |
| Applying uncommitted migration on other environments | Schema mismatch | Commit migration, document upgrade path |
| Replacing Google search fallback | OTT sync behavior change | Gate behind research queue with confidence rules |
| Frontend routing vs API URL structure | SEO URL changes later | Plan slug-based URLs before building frontend |

---

## 18. Recommended Improvements (Incremental — No Redesign)

1. Commit uncommitted metadata work on a feature branch after review
2. Populate `.env.example` and root `README.md`
3. Consolidate `connection.py` / `session.py`
4. Enhance `/health` with DB + Redis checks
5. Run platform seed script (`ott_platforms` has 0 rows)
6. Batch-enrich metadata for existing 12,281 movies (background job)
7. Build React frontend consuming existing APIs
8. Add people API endpoints reusing `Person` + `MovieCredit` models
9. Implement OTT research queue as new tables (additive migration)
10. Replace Google scraping with compliant research providers
11. Wire Celery/APScheduler in Docker for daily jobs
12. Add Redis caching for expensive list/search queries
13. Add PostgreSQL full-text search indexes
14. Implement data-quality dashboard
15. Add proper pytest configuration and CI

---

## 19. Architecture Decision: Movie-Only Scope

Per project rules:

- **Do NOT implement TV functionality**
- TV models (`tv_show.py`, `tv_show_ott.py`) remain in schema but are excluded from API, frontend, and ingestion
- All new features target `movies` and related tables only
- YouTube/trailer discovery is explicitly deferred

---

*This audit was performed read-only. No files were modified except creation of this document and `IMPLEMENTATION_PLAN.md`.*
