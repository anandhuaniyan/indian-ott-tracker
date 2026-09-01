# Administration

The operational control center uses these signed-session routes: `/admin`, `/admin/requests`, `/admin/movies`, `/admin/discovery`, `/admin/comments`, `/admin/data-health`, `/admin/images`, `/admin/ott-research`, `/admin/ott-gold-set`, `/admin/jobs`, `/admin/notifications`, `/admin/sources`, and `/admin/system-health`. No API key is included in frontend code.

The dashboard reports real catalogue, discovery, request, moderation, OTT, image, trailer, IMDb, email, job, and source-health counts. It shows the last morning and evening discovery outcomes, next local-time run, today’s discovered/imported/review/failure counts, and a stale alert after two missed successful opportunities. Alerts link directly to filtered work queues. Recent activity combines durable administrator audit events with newly submitted requests.

Discovery provides paginated status/language filters, recent per-language run history, safe errors, local movie links, and a Needs Review queue. Administrators can map an uncertain candidate to an existing movie or classify it as duplicate, wrong language, TV series, or ignored without deleting its audit history.

Requests support server-side search, status/email/local-movie/SLA filters, sorting, pagination, and the `PENDING`, `REVIEWING`, `FOUND`, `ADDED`, `REJECTED` workflow. `/admin/requests/:requestId` shows verified provider data, requester details, local catalogue status, cast/director/runtime, OTT evidence, trailer and IMDb state, a completeness checklist, email delivery state, and valid next actions. Existing local movies are informational and never prevent a request from being persisted.

Movies provide searchable, filterable, paginated operational coverage with links and targeted metadata, image, trailer, IMDb, and OTT actions. Comments, data-health, image-health, OTT research, notifications, and jobs retain their focused retry/moderation controls. OTT research includes fact-specific confidence, observations, reconciliation history/reasons, provider health/budgets, source and language coverage, source agreement, per-movie health, conflicts, and bounded refresh controls. OTT Gold Set holds the operator’s trusted expected facts and shows the publication accuracy gate; generation alone does not mark any case verified. The system-health page checks the API, database, Redis, Celery worker, scheduler, and deployed frontend/backend, and displays the durable administrator audit trail.

The Sources page reports TMDB, Tavily, YouTube metadata, SMTP, OTTplay, JustWatch, Streaming Availability, and Watchmode configuration/health without exposing credentials. Optional providers are disabled unless explicitly configured and licensed; unmatched discovery rows remain visible for manual catalogue mapping.

Login is rate-limited to five attempts per five minutes. The password is never stored in plain text. Logout deletes the cookie; sessions expire after eight hours.
