# Public sitemap verification — 2026-09-07

The reported Search Console "Couldn't fetch" state was not reproducible with public GET requests.
Before changes the index and all four children returned 200 and valid XML. HEAD returned 405.
Origin logs also showed successful Googlebot requests. Reverse DNS and forward DNS both verified
the observed 66.249.76.32 / .33 addresses as Googlebot; during this run 66.249.70.162 also fetched
the second people sitemap successfully and was verified the same way. Cloudflare was not blocking
these requests. This establishes successful fetching, not Search Console report processing.

Changes: added HEAD support; removed duplicate platform URLs, normalized provider aliases using the
public catalogue's helper, excluded empty genres and search/request/support utility pages, and made
movie ordering deterministic. Kept the existing sitemap index because total URLs exceed 50,000.

## Public results after deployment

- Index: `https://otttracker.in/sitemap.xml`, GET and HEAD 200, application/xml, no redirects.
- Static: 87 URLs; movies: 12,461; people-1: 40,000; people-2: 34,555.
- Total: 87,103 unique URLs at test time. Counts change as the catalogue updates.
- All XML parsed with the exact sitemap namespace, UTF-8 declaration and escaped URL values.
- Every location uses https://otttracker.in/, with no query strings, internal hosts or private paths.
- All children remain below 50,000 URLs and 50 MB uncompressed; the largest was 2,229,077 bytes.
- Normal and Googlebot-like curl GETs, with identity and negotiated compression, passed.
- Index/child fetch times ranged from 0.075 to 0.336 seconds in the recorded run.
- No authentication, session cookies, JavaScript or consent required; no Cloudflare challenge observed.
- robots.txt returned 200 and the exact root sitemap declaration. Google-Extended restrictions in
  Cloudflare's managed robots section are distinct from ordinary Googlebot crawling.
- Sample page responses and rendered canonical/noindex metadata passed for home, movie 1, person 1,
  Malayalam and aha. XML locations were fully validated; we did not crawl all 87,103 detail pages.
- The frontend remains a React SPA: detailed page metadata/content requires rendering JavaScript.
  The sitemap and robots responses themselves are complete without JavaScript.

## Deployment and tests

Only the API was restarted (code is bind-mounted) and the frontend rebuilt/recreated. Database
migration revision was already at head; no new migrations, database edits, port changes, DNS/tunnel
changes or unrelated container restarts were performed. API and frontend health checks passed.
63 frontend tests and 15 sitemap tests passed. Existing uncommitted work was preserved.

Cookie production tests passed for first visit, Allow All, individual/reject choices, SPA navigation,
refresh, reopening, clearing storage, consented GA4 collection, denied-storage behavior, single GA
installation, and desktop/mobile layout. Advertising personalization remains denied; a custom
banner alone is not a certified Google CMP. See `PRIVACY_AND_CONSENT.md`.

## Search Console next step

Re-submit the same root sitemap URL in the otttracker.in Domain property's Sitemaps report and
check its next "Last read" result. The endpoint is working now; no different URL is necessary.
Search Console processing is asynchronous and the displayed status was not directly inspected.
If a new fetch still fails, compare its timestamp to Cloudflare Security Events and origin access
logs rather than changing DNS or relaxing all security rules.

Reference: [Google sitemap requirements](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).
