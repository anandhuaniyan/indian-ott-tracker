# Notifications

`NotificationService` independently attempts Discord, Telegram Bot API and SMTP email delivery. Discord first uses the configured local existing-bot HTTP adapter (`DISCORD_BOT_ENDPOINT` and optional `DISCORD_BOT_SHARED_SECRET`), then falls back to `DISCORD_WEBHOOK_URL` when no adapter is configured. It does not create or sign in a competing bot. One channel exception is safely logged and cannot stop the others. Every attempted channel is persisted; successful rows have `last_notified_at`, while failed rows remain visible to administrators.

Fingerprints and cooldown windows suppress repeat delivery after success. Events cover new movie requests, OTT conflicts, Celery task failures (including TMDB/OTT/image/health jobs) and a daily serious-health/research-failure summary. Configure any subset of channels; missing credentials disable only that channel.

New-request Telegram/Discord messages include the verified title and IDs, release/language/requester data and an Admin link. Requester SMTP events `MATCHED`, `OTT_FOUND`, and `NEEDS_INFORMATION` are recorded in `request_communications` with attempt/success/failure state and idempotent fingerprints. These deliveries always occur after request persistence; failure never deletes or rolls back the request.
