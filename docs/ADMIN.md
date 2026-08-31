# Administration

The operational control center uses these signed-session routes: `/admin`, `/admin/requests`, `/admin/movies`, `/admin/comments`, `/admin/data-health`, `/admin/images`, `/admin/ott-research`, `/admin/jobs`, `/admin/notifications`, `/admin/sources`, and `/admin/system-health`. No API key is included in frontend code.

The dashboard reports real catalogue, request, moderation, OTT, image, trailer, IMDb, email, job, and source-health counts. Alerts link directly to filtered work queues. Recent activity combines durable administrator audit events with newly submitted requests.

Requests support server-side search, status/email/local-movie/SLA filters, sorting, pagination, and the `PENDING`, `REVIEWING`, `FOUND`, `ADDED`, `REJECTED` workflow. `/admin/requests/:requestId` shows verified provider data, requester details, local catalogue status, cast/director/runtime, OTT evidence, trailer and IMDb state, a completeness checklist, email delivery state, and valid next actions. Existing local movies are informational and never prevent a request from being persisted.

Movies provide searchable, filterable, paginated operational coverage with links and targeted metadata, image, trailer, IMDb, and OTT actions. Comments, data-health, image-health, OTT research, notifications, and jobs retain their focused retry/moderation controls. The system-health page checks the API, database, Redis, Celery worker, scheduler, and deployed frontend/backend, and displays the durable administrator audit trail.

The Sources page reports TMDB, Tavily, YouTube metadata, SMTP, OTTplay, and JustWatch configuration and last-run state without exposing credentials. OTTplay and JustWatch are disabled unless an authorized JSON adapter is explicitly configured; unmatched rows remain visible for manual catalogue mapping.

Login is rate-limited to five attempts per five minutes. The password is never stored in plain text. Logout deletes the cookie; sessions expire after eight hours.
