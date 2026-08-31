# OTT intelligence and accuracy policy

The India OTT pipeline follows one rule: **UNKNOWN is better than wrong**. `OttAvailability` remains the canonical public projection, while evidence, observations, decisions, provider state, cache entries, and the gold accuracy set are retained independently. A current availability observation is never converted into an original OTT release date.

## Facts and public rules

The engine treats platform availability, availability type, announcement date, generic digital date, observed availability, and original OTT premiere as different facts. Country is always explicit and automated public collection is restricted to `IN`. Availability types are `SUBSCRIPTION`, `FREE`, `ADS`, `RENT`, `BUY`, `CHANNEL`, or `UNKNOWN`; rent and buy offers are not presented as subscription streaming.

The public movie page may show a confirmed India platform even when its original release date is unknown. An exact date is returned only when the canonical row is `CONFIRMED`. Home “Upcoming OTT”, “Recently released on OTT”, and the OTT calendar already require a confirmed canonical date, so digital purchase dates, observations, theatrical dates, and article publication dates cannot pad those sections.

`first_seen_at`, `last_seen_at`, and `observed_available_from` describe what this system observed. They do not claim an original premiere date. Multiple current/later platforms can coexist; `is_original_premiere` identifies the earliest sufficiently confirmed original premiere without deleting later availability.

## Provider stack

Provider-specific code lives under `app/services/ott/providers/` and emits `NormalizedOttEvidence`.

- TMDB/JustWatch uses `/movie/{tmdb_id}/watch/providers`, reads only `IN`, retains `flatrate`, `free`, `ads`, `rent`, and `buy` distinctions, and supplies platform evidence with JustWatch attribution. TMDB release type 4 is stored separately as low-confidence `DIGITAL_DATE` evidence and cannot publish a subscription OTT date.
- Streaming Availability is optional through `STREAMING_AVAILABILITY_ENABLED` and `STREAMING_AVAILABILITY_API_KEY`. It prefers IMDb ID, then TMDB ID, requests India only, and treats `availableSince` as an observation boundary rather than a premiere date.
- Watchmode is optional through `WATCHMODE_ENABLED` and `WATCHMODE_API_KEY`. It requests region `IN` and can be disabled without affecting other providers. Operators must confirm commercial licensing, caching, attribution, and retention terms before production enablement.
- OTTplay discovery remains opt-in through an operator-authorized JSON adapter. The application does not bypass robots, authentication, CAPTCHA, or anti-bot controls. Matched and unmatched discoveries remain durable in `ott_source_releases`.
- Official platform/studio/distributor, reputable-news, targeted Tavily, and manual adapters normalize inspected evidence. Search snippets are discovery leads, not automatically verified facts. Tavily remains tightly budgeted and is never run across the full catalogue.

The cheapest-first per-movie order is cache, TMDB/JustWatch, Streaming Availability, then optional Watchmode. Daily OTTplay discovery and the existing targeted research queue run as separate bounded jobs. One provider’s timeout, 403, 429, exhausted quota, or outage is never translated into `NOT_FOUND` and does not stop the remaining providers.

## Identity and reconciliation

`MovieMatchService` matches exact TMDB or IMDb IDs first. Title matching also requires enough corroboration from normalized/original/alternate title, release year, original language, director, cast, and runtime. Title-only or ambiguous same-title matches are rejected or sent to review; regional remakes and same-name films are not silently merged.

`OTTReconciliationService` scores movie identity, platform, and date separately. Manual evidence and official platform evidence have the highest authority. A single availability provider can establish platform-only availability, but not a release date. Non-official dates require independent agreement. Credible disagreement becomes `CONFLICTING`/`NEEDS_REVIEW`, preserves every source, creates a data-quality issue, and leaves the public date hidden. An inspected official correction supersedes weaker rows without deleting them. A manual row locks canonical automation until an administrator explicitly changes it.

Every decision is appended to `ott_reconciliation_decisions`; the previous decision is marked non-current. `supporting_evidence_ids`, `conflicting_evidence_ids`, a reason, three confidence values, and a per-movie 0–100 health score explain the result.

## Provider controls and automation

`OTTApiBudgetManager` keeps daily and monthly counters per provider. Zero means no application-level limit; production credentials should always use realistic values. `OttProviderControlService` records request/success/error/match counts, latency, safe errors, consecutive failures, and the states `HEALTHY`, `DEGRADED`, `RATE_LIMITED`, `QUOTA_EXHAUSTED`, `DOWN`, and `DISABLED`. Repeated failures open a timed circuit breaker. API keys and authorization values are redacted from errors and HTTP request logging is restricted.

Provider responses are cached with short TTLs for upcoming/recent films and longer TTLs for stable historical films. `operations.ott_intelligence_daily` prioritizes requested movies, due confirmed releases, recent theatrical titles, and popularity in bounded batches. `operations.ott_intelligence_weekly` revisits platform-only records and conflicts. Durable `operation_states` records processed totals, provider outcomes, safe errors, and next run. The compatibility `app.services.tmdb.sync_ott` command now invokes this bounded pipeline instead of querying all movies and writing a parallel canonical table.

Release-day monitoring preserves an announced date when a provider is temporarily absent. Negative observations mean only “this provider returned no India availability at this check”; they do not mean the movie has no OTT release.

## Gold set and phased rollout

Migration `a6b7c8d9e0f1` adds the accuracy engine. The first live gold set contains 100 existing movies: 20 each in Malayalam, Tamil, Telugu, Hindi, and Kannada, sampled across upcoming, recent, old, platform-only, no-OTT, and popular candidates. Generation never invents expected values. Administrators must fill trusted expected platform/date/state/source values and mark each case verified.

The evaluator reports platform precision, date precision, and false dates. The gate requires all 100 cases, at least 95% measured platform precision, at least 98% measured date precision, and zero false published dates. `OTT_INTELLIGENCE_AUTO_PUBLICATION_ENABLED` defaults to `false`; a passing report does not silently change configuration. Production publication is an explicit operator decision after licensing and manual gold-set review.

Only then should operators enable phased work: 0–90 days, 91–180 days, 181–365 days, popular/requested older titles, and finally remaining current-platform coverage. Historical original dates should be researched only when valuable; “platform known, date not confirmed” is a correct outcome.

## Admin workflow

Admin `/admin/ott-research` is the OTT command center. It reports canonical states, source coverage, provider budgets/health, source agreement, language coverage, conflicts, observations, immutable decisions, health score, and the reason behind each selection. It can run bounded daily/weekly collection or refresh one movie. `/admin/ott-gold-set` generates, filters, edits, and evaluates manual ground truth. Existing Sources pages retain unmatched OTTplay/authorized-feed mapping. Manual verification accepts a platform-only fact when the true date is unknown.

Before enabling any provider, verify its current commercial-use permission, quota, attribution, caching, and retention requirements. No external key is committed to Git.
