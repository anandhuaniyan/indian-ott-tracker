# OTT research

`OttAvailability` is canonical public data. `OttEvidence` stores every attempt, status, platform, release date, source URL/title, confidence, attempts, last/next check and notes. Supported states are `UNKNOWN`, `QUEUED`, `RESEARCHING`, `POSSIBLE`, `CONFIRMED`, `CONFLICTING`, `NOT_FOUND`, `NEEDS_REVIEW` and `FAILED`.

The research provider is opt-in through `OTT_SEARCH_API_URL` and `OTT_SEARCH_API_KEY`; it must be a lawful API that returns result URLs and may return structured platform/release date fields. Consumer search pages are never scraped.

Source scores are high for official OTT platforms, medium-high for established trade/entertainment publications, and low for unknown aggregators/blogs. Evidence below `OTT_CONFIRMATION_THRESHOLD` stays `POSSIBLE` and cannot publish canonical availability. Strong confirmed evidence creates or updates `OttAvailability` only when it is at least as confident as the existing value.

Credible disagreement becomes `CONFLICTING`, preserves all evidence, opens a high-severity issue and notifies administrators. It never overwrites stronger canonical data. Retry calculation differs for unknown, possible, not-found, conflicting, failed and confirmed future/past records; Beat queues and processes only due records. A verification task requeues stale canonical availability.
