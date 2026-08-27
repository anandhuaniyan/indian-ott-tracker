# SEO

Every public page sets a distinct title, description, canonical URL, Open Graph and Twitter metadata. Movie and person pages emit value-conditional Movie/Person JSON-LD and BreadcrumbList; home emits WebSite with SearchAction. Unknown values are omitted.

`/sitemap.xml` escapes XML and includes home, discover/search, all six calendar routes, genres, the five V1 languages, OTT landing/platforms, movies, people, requests and legal pages. Admin and APIs are excluded. With the current catalogue it remains below the 50,000 URL sitemap limit; introduce a sitemap index before exceeding that limit.

`robots.txt` allows public pages and blocks API/admin crawling. `GOOGLE_SITE_VERIFICATION` is passed as `VITE_GOOGLE_SITE_VERIFICATION` by Docker and becomes a verification meta tag. Analytics loads only when `VITE_GA_MEASUREMENT_ID` is configured and analytics consent is true.
