# Background jobs

Celery registers and Beat schedules: TMDB incremental sync, bounded metadata enrichment, IMDb rating refresh, whole-catalogue data health, image health, image recovery, OTT queueing, research, verification, daily notifications and cleanup. Redis is both broker and result backend.

Data, metadata and image tasks persist cursors. Celery success/failure signals update job state for every task; failure signals also dispatch an administrator notification without altering retry behavior. Tasks acknowledge late and external/operational tasks use bounded batches and retry backoff.

`ratings.imdb_refresh` runs every six hours in batches of 25. It prioritizes missing ratings, then recently released and popular movies; recent scores become due daily, popular scores every three days and older scores every 30 days. It runs only when the configured lawful rating provider credentials are present and records progress in `operation_states`.

Inspect registration with `docker compose exec worker celery -A app.workers.celery_app.celery_app inspect registered` and Beat logs with `docker compose logs beat`.
