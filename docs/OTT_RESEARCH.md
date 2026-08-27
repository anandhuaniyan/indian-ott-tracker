# OTT research

`OttAvailability` is canonical public data. `OttEvidence` stores every attempt, status, platform, release date, source URL/title, confidence, attempts, last/next check and notes. Supported states are `UNKNOWN`, `QUEUED`, `RESEARCHING`, `POSSIBLE`, `CONFIRMED`, `CONFLICTING`, `NOT_FOUND`, `NEEDS_REVIEW` and `FAILED`.

The research provider is opt-in. A generic lawful JSON API uses `OTT_SEARCH_API_URL` and `OTT_SEARCH_API_KEY`. Google Programmable Search uses the official Custom Search JSON API with `GOOGLE_SEARCH_API_KEY` and `GOOGLE_SEARCH_ENGINE_ID`; Google result HTML is never scraped. Google searches use multiple title/year/language/platform queries, deduplicate URLs, infer known platform domains and parse explicit dates for evidence review.

Source scores are high for official OTT platforms, medium-high for established trade/entertainment publications, and low for unknown aggregators/blogs. Evidence below `OTT_CONFIRMATION_THRESHOLD` stays `POSSIBLE` and cannot publish canonical availability. Strong confirmed evidence creates or updates `OttAvailability` only when it is at least as confident as the existing value.

Credible disagreement becomes `CONFLICTING`, preserves all evidence, opens a high-severity issue and notifies administrators. It never overwrites stronger canonical data. Retry calculation differs for unknown, possible, not-found, conflicting, failed and confirmed future/past records; Beat queues and processes only due records. A verification task requeues stale canonical availability.

`operations.ott_backfill` checkpoints every movie that has no canonical provider or has a canonical row without a release date. It creates at most one active evidence queue item per movie. Missing search credentials are shown as configuration blockers in the admin progress response and do not create fabricated `NOT_FOUND` results.
