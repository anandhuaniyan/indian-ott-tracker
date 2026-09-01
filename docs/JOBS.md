# Background jobs

Celery registers and Beat schedules: twice-daily movie discovery, bounded metadata enrichment, IMDb rating refresh, whole-catalogue data health, image health, image recovery, daily release-status classification, OTT eligibility queueing, targeted research, verification, the daily and weekly OTT intelligence pipelines, weekly gold-set evaluation, optional lawful release-source sync, daily notifications and cleanup. Redis is both broker and result backend.

`movies.discovery` runs at 08:00 and 20:00 in `SITE_TIMEZONE` (default `Asia/Singapore`), not at corresponding UTC wall-clock hours. Every run scans Malayalam, Tamil, Telugu, Hindi and Kannada independently from 60 days in the past through 180 days ahead. It checks TMDB ID, IMDb ID, then normalized title/year/language; only a new high-confidence TMDB identity is imported. Ambiguous identities and unmatched configured release-source rows remain in Admin → Discovery. A failure in one language or source does not discard other results. Run, per-language and per-source counts are persisted in `movie_discovery_runs`, while stable decisions are retained in `movie_discovery_candidates`.

`movies.discovery_weekly` runs Sunday at 03:30 site time and reconciles the wider previous-365/next-365-day window. Newly imported movies immediately receive the verified rich TMDB payload and are queued for existing repair and targeted OTT intelligence tasks. Both regular schedules and their next/last results are visible on the Admin dashboard.

`operations.ott_intelligence_daily` collects cached/direct India availability in priority order and a bounded batch; `operations.ott_intelligence_weekly` revisits platform-only/conflicting movies. `operations.ott_intelligence_movie` is used by requested/existing titles and targeted Admin refresh. Provider failure is isolated, budgeted, cached, and circuit-broken. None of these jobs converts an HTTP failure into `NOT_FOUND`, and automatic publication remains disabled until the gold accuracy gate is manually satisfied and configuration is explicitly changed.

Data, metadata and image tasks persist cursors. Celery success/failure signals update job state for every task; failure signals also dispatch an administrator notification without altering retry behavior. Tasks acknowledge late and external/operational tasks use bounded batches and retry backoff.

`ratings.imdb_refresh` runs every six hours in batches of 25. It prioritizes missing ratings, then recently released and popular movies; recent scores become due daily, popular scores every three days and older scores every 30 days. It runs only when the configured lawful rating provider credentials are present and records progress in `operation_states`.

Inspect registration with `docker compose exec worker celery -A app.workers.celery_app.celery_app inspect registered` and Beat logs with `docker compose logs beat`.

## Accelerated initial and repair backfills

The conservative Beat schedule is separate from administrator-started backfills. The Jobs page can start or resume `tmdb.metadata_backfill`, `tmdb.person_backfill`, `operations.image_backfill`, `ratings.imdb_backfill`, `operations.ott_backfill`, or the sequential `operations.repair_orchestrator`. Batch sizes are configurable through the matching `*_BACKFILL_BATCH_SIZE` variables.

`operation_states` stores status, cursor, attempted/completed totals, last success/failure/error, completion time, and structured details such as source statistics and next-run time. `backfill_records` checkpoints every movie/person with attempts and error text. Successful records are never repeated, failures stop after `BACKFILL_MAX_ATTEMPTS`, completed runs do not automatically restart, and a stale interrupted run can be resumed. Release classification uses its own additive `release_status_classification` state and does not reset any metadata, people, image, IMDb, or earlier OTT checkpoint. The orchestrator deliberately performs metadata, people, images, IMDb, eligible OTT queue/research, and a whole-database health pass in sequence rather than saturating providers concurrently.

Movie detail requests may enqueue `repair.movie` only when critical metadata is missing. `on_demand_repair:<movie-id>` provides cooldown and deduplication. The task updates metadata, associated people, images, approved IMDb ratings, OTT queue state, and targeted data-health issues. Administrators can explicitly queue the same repair from the Jobs page.
