# SEO

Every public page sets a distinct title, description, canonical URL, Open Graph and Twitter metadata. Movie and person pages emit value-conditional Movie/Person JSON-LD and BreadcrumbList; home emits WebSite with SearchAction. Unknown values are omitted. `frontend/index.html` also ships static title/description/OG/canonical metadata (built with `%VITE_SITE_URL%`).

Sitemaps are split because the live catalogue exceeds Google's 50,000-URL per-file limit:

- `/sitemap.xml` is a **sitemap index** pointing to `/sitemaps/static.xml` (home, discover, all six calendar routes, languages, populated genres, normalized OTT landing/platforms, legal pages), `/sitemaps/movies.xml` (movies + `lastmod`), and `/sitemaps/people-N.xml` (people in chunks of ≤ 40,000). Search, request/support utilities and admin are excluded. Platform aliases are normalized with the same helper as the public catalogue; duplicate URLs are removed. `robots.txt` allows public pages and the public read-only `/api/v1/` (the SPA's data layer must be crawlable so Googlebot's renderer can load content), while blocking `/api/v1/admin`, `/api/v1/deep-search`, `/admin`, `/deep-search` and `?mode=deep`.

GET and HEAD are supported for robots.txt, the sitemap index and all child sitemaps. These endpoints
are public server-generated responses with no cookies, authentication or JavaScript requirements.
Movie URLs have deterministic ID ordering. The movie file currently has 12,461 URLs; split it before
it reaches 50,000. See `SITEMAP_VERIFICATION.md` for the public production checks and limits.

`GOOGLE_SITE_VERIFICATION` is passed as `VITE_GOOGLE_SITE_VERIFICATION` by Docker and becomes a verification meta tag. GA4 loads only when `VITE_GA_MEASUREMENT_ID` is configured; it operates under consent mode v2 (defaults denied) and sends its own `page_view` per route plus app events — see `GOOGLE_ANALYTICS.md`. AdSense remains consent-gated — see `ADSENSE.md` / `ADSENSE_SETUP.md`.

Setup and verification workflow: `GOOGLE_SEARCH_CONSOLE.md`. Code for all SEO endpoints lives in `app/main.py` + `app/seo/`; keep CSP in `app/main.py` and `frontend/nginx.conf` in sync.
