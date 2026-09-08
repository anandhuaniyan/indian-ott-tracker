# Google Analytics (GA4)

The frontend ships with GA4 support that is **disabled until you configure identifiers**. No source code
changes are needed to enable it.

## Prerequisites

1. Create a **Google Analytics 4** property at https://analytics.google.com (or reuse an existing one).
2. Copy its **Measurement ID**, e.g. `G-XXXXXXXXXX`.

## Enable

Provide these variables at build time (Docker Compose passes `VITE_*` values into the frontend Docker build — do not
commit real values):

| Variable | Value |
| --- | --- |
| `VITE_GA_MEASUREMENT_ID` | `G-XXXXXXXXXX` |

Set `VITE_GA_MEASUREMENT_ID` in the project-root `.env`, then rebuild and redeploy only the frontend:

```powershell
docker compose build frontend
docker compose up -d --no-deps frontend
```

Runtime environment changes alone cannot update a compiled Vite bundle. No source edits are required.

## Consent behaviour

- A **consent mode v2** default (`denied`) is declared as soon as gtag initialises, matching the visitor's
  stored preference (`ott-consent` in `localStorage`).
- `analytics_storage` is `granted` **only** when the visitor accepted the Analytics category.
- Changing preferences dispatches a `consent-updated` event (fired by `Consent.jsx` before reload), which
  `Tracking.jsx` turns into a `consent` `update`. The preference UI then reloads the document.
- `anonymize_ip` is enabled and `send_page_view` is disabled; the app sends its own `page_view` per route.

## Events sent

Fired from `frontend/src/services/analytics.js` (all event methods are no-ops unless gtag is configured):

| Event | Where | Params (no PII) |
| --- | --- | --- |
| `page_view` | every route change | `page_path`, `page_location`, `page_title` |
| `search` | Discover "Apply" with a `q` | `search_term`, `language`, `genre`, `platform` |
| `filter_used` | Discover "Apply" without `q` | `filter_name`, `filter_value` (one per active filter) |
| `movie_view` | movie detail page load | `movie_id`, `movie_title`, `m_language` |
| `person_view` | person detail page load | `person_id`, `person_name` |
| `ott_platform_click` | "Watch on …" / source link | `movie_id`, `movie_title`, `ott_platform` |
| `movie_request_started` / `movie_request_submitted` | Request form | `movie_id` (numeric external id only) |

**PII rule:** nothing personal — names, email addresses or free-text input — is ever passed to gtag. Keep it
that way when adding events.

## Verify

1. Rebuild + redeploy frontend.
2. Accept the Analytics consent on the live site.
3. In GA4 open **Reports → Realtime** and click around the site; events should appear within seconds.

## If events are missing

- Confirm `VITE_GA_MEASUREMENT_ID` is set in the build environment and the rebuild reused it.
- Consent must be granted (the banner defaults everything to denied).
- Content-Security-Policy already allows `googletagmanager.com`, `google-analytics.com` and
  `analytics.google.com` (both in `frontend/nginx.conf` and `app/main.py` — keep them in sync).
- Check the browser console for blocked-request messages.

## Production repair verified 2026-09-07

The production build already contained the configured measurement ID. The actual failure was
`window.gtag = (...args) => window.dataLayer.push(args)`: Google did not process those arrays as
gtag commands. Use the documented `function gtag() { dataLayer.push(arguments); }` format.
Consent defaults are queued before configuration and before loading the asynchronous script.

Before repair, the live Google script returned HTTP 200 but no collection requests occurred,
even after granting analytics consent. After repair, the live homepage and `/discover` SPA
navigation each produced a `page_view` with a Google `/g/collect` HTTP 204 response. One script
and one config command were present per document; navigation did not reinitialize GA.

The nginx document CSP additionally permits `region1.google-analytics.com` and the observed
fallback endpoint `https://www.google.com/g/collect`, without enabling wildcard scripts or
unsafe-inline. API response headers do not govern the frontend document's Google requests.
The existing blocked Cloudflare Insights beacon is separate from GA and was left unchanged.

The fresh public bundle matched the running container byte-for-byte; Cloudflare returned
`DYNAMIC` for the HTML. Only the frontend container was replaced. All 53 frontend tests passed.

Consent Mode currently loads the configured Google tag with denied storage defaults and can
send cookieless pings before consent. Accept Analytics via the footer's Cookie preferences to
test consented measurement. Do not equate a queued event or cookieless ping with a guaranteed
Realtime active user. Collection HTTP 204 confirms transport, not the account's report filters.
If Realtime remains empty, refresh the site, check the correct property/web stream, allow a few
minutes, and inspect active internal/developer traffic filters and browser blocking extensions.
Keep enhanced-measurement history page views disabled when using the app's manual SPA events.

References: [Google tag command format](https://developers.google.com/tag-platform/gtagjs),
[Google CSP guidance](https://developers.google.com/tag-platform/security/guides/csp).
