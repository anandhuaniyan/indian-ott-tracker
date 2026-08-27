# Administration

Routes are `/admin`, `/admin/requests`, `/admin/data-health`, `/admin/images`, `/admin/ott-research`, `/admin/jobs` and `/admin/notifications`. They use the signed admin session; no API key is included in frontend code.

The dashboard reports total movies, affected movies, open/image/OTT issues, conflicts, queue/failures, pending requests, notifications and job health. Requests support search, status filtering, full details and `PENDING`, `REVIEWING`, `FOUND`, `ADDED`, `REJECTED` updates. Health and notification pages support server-side filters/pagination. Image and OTT pages provide retry/requeue/review actions. Job state shows cursors, processed counts, last success/failure and errors.

Login is rate-limited to five attempts per five minutes. The password is never stored in plain text. Logout deletes the cookie; sessions expire after eight hours.
