# Architecture

The movie-only V1 has four runtime layers:

1. React/Vite renders public and cookie-authenticated admin routes. Public route modules are code-split with `React.lazy`; API GETs share a short-lived promise cache and all artwork is lazy, responsive and failure-safe.
2. FastAPI owns validation, discovery queries, categorized search, movie/person projections, SEO XML/text endpoints, requests and admin APIs. SQLAlchemy expressions and bound parameters are used throughout.
3. PostgreSQL stores the existing movie catalogue plus normalized genres, languages, credits, people, releases, ratings, artwork, production records, alternative titles, canonical OTT availability and operational records. OTT evidence, observation history, immutable reconciliation decisions, provider budgets/health/cache, and the manual accuracy gold set remain separate from the public projection.
4. Redis is the Celery broker/result backend and rate-limit store. Celery worker and Beat execute bounded tasks. `operation_states` persists cursors, processed counts, success/failure timestamps and errors so repeated runs eventually cover the full catalogue.

Public card DTOs derive their primary rating only from the `movie_ratings` IMDb record and omit internal TMDB identifiers. The configurable `MovieRatingProvider` implementation uses the stored IMDb external ID with an approved API; it never scrapes IMDb. Calendar responses expose separate theatrical records and confirmed canonical OTT dates. Current India platform observations and generic digital dates cannot become calendar dates.

The OTT intelligence layer uses normalized provider adapters, an ID-first regional-movie matcher, per-provider caches/budgets/circuit breakers, and a fact-specific reconciler. Provider collection is allowed while automatic publication is independently gated by a manually verified 100-movie accuracy set. See `OTT_RESEARCH.md` for precedence and rollout policy.

The legacy TV tables remain untouched but are not exposed in V1. Metadata enrichment is additive and keyed by existing movie/TMDB IDs. No task deletes or replaces the catalogue.

Authentication uses a PBKDF2 password hash and an eight-hour `itsdangerous` signed, HttpOnly, SameSite=Strict session cookie. Production adds Secure and HSTS. Same-origin checks protect cookie-authenticated mutations. `ADMIN_API_KEY` remains server-side for legacy automation endpoints only.
