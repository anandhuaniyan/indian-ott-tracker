# Background jobs

Celery registers and Beat schedules: TMDB incremental sync, bounded metadata enrichment, IMDb rating refresh, whole-catalogue data health, image health, image recovery, OTT queueing, research, verification, daily notifications and cleanup. Redis is both broker and result backend.

Data, metadata and image tasks persist cursors. Celery success/failure signals update job state for every task; failure signals also dispatch an administrator notification without altering retry behavior. Tasks acknowledge late and external/operational tasks use bounded batches and retry backoff.

`ratings.imdb_refresh` runs every six hours in batches of 25. It prioritizes missing ratings, then recently released and popular movies; recent scores become due daily, popular scores every three days and older scores every 30 days. It runs only when the configured lawful rating provider credentials are present and records progress in `operation_states`.

Inspect registration with `docker compose exec worker celery -A app.workers.celery_app.celery_app inspect registered` and Beat logs with `docker compose logs beat`.

## Accelerated initial and repair backfills

The conservative Beat schedule is separate from administrator-started backfills. The Jobs page can start or resume `tmdb.metadata_backfill`, `tmdb.person_backfill`, `operations.image_backfill`, `ratings.imdb_backfill`, `operations.ott_backfill`, or the sequential `operations.repair_orchestrator`. Batch sizes are configurable through the matching `*_BACKFILL_BATCH_SIZE` variables.

`operation_states` stores status, cursor, attempted/completed totals, last success/failure/error, and completion time. `backfill_records` checkpoints every movie/person with attempts and error text. Successful records are never repeated, failures stop after `BACKFILL_MAX_ATTEMPTS`, completed runs do not automatically restart, and a stale interrupted run can be resumed. The orchestrator deliberately performs metadata, people, images, IMDb, OTT queue/research, and a whole-database health pass in sequence rather than saturating providers concurrently.

Movie detail requests may enqueue `repair.movie` only when critical metadata is missing. `on_demand_repair:<movie-id>` provides cooldown and deduplication. The task updates metadata, associated people, images, approved IMDb ratings, OTT queue state, and targeted data-health issues. Administrators can explicitly queue the same repair from the Jobs page.
