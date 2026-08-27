# Database

Core movie tables are `movies`, `genres`, `languages`, their association tables, `people`, `movie_credits`, `alternative_titles`, `keywords`, `movie_keywords`, `movie_images`, `movie_release_dates`, `movie_ratings`, production companies/countries and external IDs. `ott_availability` is the public canonical OTT source of truth; `ott_evidence` is the research/audit history.

Operational tables are `movie_requests`, `data_quality_issues`, `notification_logs` and `operation_states`. Health issues are deduplicated by subject/type in service logic and resolved automatically when later scans find the field healthy. Image issues may identify either a movie or person.

Migration `f6a7b8c9d0e1` adds collection/alternative-title storage, person image issue ownership, processed/failure job facts, and indexes for language/release discovery, popularity, rating, creation time, credits and OTT provider/status/date. All migrations are additive and preserve existing movie rows.

Run migrations with `.\.venv\Scripts\python.exe -m alembic upgrade head`. Back up PostgreSQL before production migrations and preserve the `ott_postgres_data` volume.
