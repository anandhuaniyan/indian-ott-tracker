# Google Search Console

## Pre-requisites

- A Google account with access to the domain's DNS (Cloudflare).
- Documentation: sitemap layout is covered here and in `SEO.md`.

## 1. Add the property

Use a **Domain property** with the bare domain so it covers all subdomains (http/https and `www.`):

1. https://search.google.com/search-console → **Add property** → **Domain**.
2. Enter `otttracker.in`.
3. Choose **DNS** verification.

Google offers a **TXT verification record**. Add it as a new DNS record in Cloudflare for the zone
`otttracker.in`. TXT records do not require a proxy toggle. DNS propagation can take minutes to hours;
return to Search Console and click **Verify** once it resolves.

## 2. Submit the sitemap index

In Search Console → **Sitemaps**, submit:

```
https://otttracker.in/sitemap.xml
```

That index references the actual files, which are served by the API and proxied by nginx:

| File | Content |
| --- | --- |
| `/sitemaps/static.xml` | home, discover, search, OTT, calendar, languages, genres, legal pages |
| `/sitemaps/movies.xml` | every movie detail URL, with `<lastmod>` |
| `/sitemaps/people-1.xml`, `people-2.xml`, … | people detail URLs in chunks ≤ 40,000 URLs |

Splitting keeps every file under Google's 50,000-URL per-file limit. Re-submit at most when the URL set
changes structurally; Google discovers new URLs over time.

## 3. What to check

- **URL inspection** of a movie page (`/movies/123`) to confirm Google renders the SPA and sees title,
  canonical and JSON-LD.
- **Index coverage** for large/duplicate groups. Expected exclusions (intentional):
  `/api/*`, `/admin`, `/deep-search` and `?mode=deep` are `Disallow`ed in the origin `robots.txt`.
- The breadth of `?mode=deep` deep-search pages is deliberately not in the sitemap.

## 4. Notes

- Search Console works on the already-deployed site; no rebuild is required for verification or sitemaps.
- `robots.txt` is composed of Cloudflare-managed rules plus the origin's own section; if a Google crawler
  is ever blocked, check the Cloudflare managed rules first.