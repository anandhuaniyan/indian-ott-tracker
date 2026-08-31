# Database

Core movie tables are `movies`, `genres`, `languages`, their association tables, `people`, `movie_credits`, `alternative_titles`, `keywords`, `movie_keywords`, `movie_images`, `movie_release_dates`, `movie_ratings`, production companies/countries and external IDs. `ott_availability` is the public canonical OTT projection; `ott_evidence` stores source facts and confidence without discarding conflicting or superseded history.

Migration `a6b7c8d9e0f1` adds fact type, availability type, external source ID, match/platform/date confidence, verification method, observation time, and supersession to evidence. Canonical rows gain manual locks, fact-specific confidence, first/last seen times, observed availability boundary, original-premiere marker, release state, health score, and supporting evidence IDs. New tables are `ott_availability_observations`, `ott_reconciliation_decisions`, `ott_provider_budget_periods`, `ott_provider_health`, `ott_provider_cache`, and `ott_gold_set_cases`.

Operational tables are `movie_requests`, `data_quality_issues`, `notification_logs` and `operation_states`. Health issues are deduplicated by subject/type in service logic and resolved automatically when later scans find the field healthy. Image issues may identify either a movie or person.

Migration `f6a7b8c9d0e1` adds collection/alternative-title storage, person image issue ownership, processed/failure job facts, and indexes for language/release discovery, popularity, rating, creation time, credits and OTT provider/status/date. All migrations are additive and preserve existing movie rows. The V3 migration was applied to the existing PostgreSQL volume with the catalogue count unchanged at 12,281.

Run migrations with `.\.venv\Scripts\python.exe -m alembic upgrade head`. Back up PostgreSQL before production migrations and preserve the `ott_postgres_data` volume.
