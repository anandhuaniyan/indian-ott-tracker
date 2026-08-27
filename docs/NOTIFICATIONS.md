# Notifications

`NotificationService` independently attempts Discord webhook, Telegram Bot API and SMTP email delivery. One channel exception is logged and cannot stop the others. Every attempted channel is persisted; successful rows have `last_notified_at`, while failed rows remain visible to administrators.

Fingerprints and cooldown windows suppress repeat delivery after success. Events cover new movie requests, OTT conflicts, Celery task failures (including TMDB/OTT/image/health jobs) and a daily serious-health/research-failure summary. Configure any subset of channels; missing credentials disable only that channel.
