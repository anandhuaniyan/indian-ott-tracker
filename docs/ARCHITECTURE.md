# Architecture

The movie-only V1 has four runtime layers:

1. React/Vite renders public and cookie-authenticated admin routes. Public route modules are code-split with `React.lazy`; API GETs share a short-lived promise cache and all artwork is lazy, responsive and failure-safe.
2. FastAPI owns validation, discovery queries, categorized search, movie/person projections, SEO XML/text endpoints, requests and admin APIs. SQLAlchemy expressions and bound parameters are used throughout.
3. PostgreSQL stores the existing movie catalogue plus normalized genres, languages, credits, people, releases, ratings, artwork, production records, alternative titles, canonical OTT availability and operational records.
4. Redis is the Celery broker/result backend and rate-limit store. Celery worker and Beat execute bounded tasks. `operation_states` persists cursors, processed counts, success/failure timestamps and errors so repeated runs eventually cover the full catalogue.

Public card DTOs derive their primary rating only from the `movie_ratings` IMDb record and omit internal TMDB identifiers. The configurable `MovieRatingProvider` implementation uses the stored IMDb external ID with an approved API; it never scrapes IMDb. Calendar responses expose separate theatrical records and high-confidence canonical OTT availability records.

The legacy TV tables remain untouched but are not exposed in V1. Metadata enrichment is additive and keyed by existing movie/TMDB IDs. No task deletes or replaces the catalogue.

Authentication uses a PBKDF2 password hash and an eight-hour `itsdangerous` signed, HttpOnly, SameSite=Strict session cookie. Production adds Secure and HSTS. Same-origin checks protect cookie-authenticated mutations. `ADMIN_API_KEY` remains server-side for legacy automation endpoints only.
