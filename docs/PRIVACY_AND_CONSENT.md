# Privacy & Consent

This document describes how consent and privacy work in this codebase, and where to revisit when legal
requirements change. It is not legal advice.

## The consent banner

- `frontend/src/components/Consent.jsx` renders a banner on first visit, offering three categories:
  **Necessary** (always on), **Analytics**, **Advertising**.
- Choices are stored in `localStorage` under `ott-consent` with an `updatedAt` timestamp.
- A first visit or invalid/cleared consent shows the banner. **Allow All** explicitly enables
  Analytics and Advertising; **Reject optional** and **Save choices** remain available.
  Stored consent must contain `necessary: true` and actual booleans for both optional categories.
  Failed storage writes display an error and do not grant consent.
- The footer's **Cookie preferences** reopens the panel (via the `open-cookie-preferences` event); saving
  writes the new values, dispatches `consent-updated`, and reloads so optional scripts stop loading.

## How consent gates Google scripts

`frontend/src/components/Tracking.jsx`:
- Loads configured `gtag.js` in advanced Consent Mode: storage defaults to denied without a valid
  saved choice, so cookieless pings can still be sent. AdSense loading requires Advertising consent.
- With no identifiers configured (`VITE_GA_MEASUREMENT_ID` / `VITE_ADSENSE_CLIENT_ID` empty), nothing loads.
- Declares **Consent Mode v2** defaults (`denied`) and updates them on `consent-updated`, so GA4 reacts to
  preference changes immediately, then reloads the page.
- `ad_personalization` stays denied even after Allow All; ad slots request non-personalized ads.
  This custom banner is not a Google-certified CMP. Before enabling AdSense for audiences requiring
  Google's certified CMP, integrate that CMP and its region-specific consent signals. An explicit
  custom-banner click alone does not satisfy every Google CMP or legal requirement.

## Production checks (2026-09-07)

Verified in isolated Chromium sessions against `https://otttracker.in`: first visit, Allow All,
Discover/Search/Movie/OTT navigation, refresh, reopening saved categories, individual settings,
clearing storage, and rejecting optional categories. GA4 collection returned 204 after consent;
rejection produced denied storage signals and no new GA cookies in a clean session. Previously
stored cookies are not automatically deleted by withdrawal; browser controls can remove them.
Only one Google tag script was installed per document. Desktop and 360px mobile screenshots were
checked, including 44px buttons and a banner that fits the viewport. Crawlers fetched XML and
robots.txt without JavaScript or consent.

## What analytics data contains

Events sent via `frontend/src/services/analytics.js` contain only aggregate, non-personal identifiers:
page paths, searched terms and active filters, numeric movie/person ids, movie titles, and ott platform
names. **Never** pass names, email addresses, or free-text submissions to analytics or advertising
services.

## Privacy & Cookies pages

Text is defined inline in `frontend/src/app/App.jsx`:
- `/privacy` → `Privacy Policy`
- `/cookies` → `Cookie Policy`

Review and update these whenever behaviour or third-party usage changes. The pages are also reachable via
search engines (they are in the static sitemap).

## Operator responsibilities

- Set the GA4 / AdSense identifiers **only after** the relevant approvals; see
  `GOOGLE_ANALYTICS.md`, `ADSENSE_SETUP.md` and `GOOGLE_SEARCH_CONSOLE.md`.
- Keep `index.html`, `Seo.jsx` and the legal pages consistent with any new third-party integrations.
- Revisit the consent text when Google's consent-mode or cookie regulations (e.g. EU/UK requirements)
  apply to your audience.
