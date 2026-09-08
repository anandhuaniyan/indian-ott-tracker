# AdSense ownership verification

Configured production identifiers in the ignored root `.env`:

- `VITE_ADSENSE_CLIENT_ID=ca-pub-3451760985606446` (frontend build argument).
- `ADSENSE_PUBLISHER_ID=pub-3451760985606446` (API environment).
- No slot ID added. GA4 remains `G-P5PVB32PP8`.

Vite emits a `google-adsense-account` meta tag into the initial HTML only for a valid configured
client ID. This lets Google verify ownership without cookies, JavaScript or advertising consent.
The existing AdSense script loader remains advertising-consent controlled, asynchronous, deduplicated,
and now includes `crossorigin="anonymous"`. It requests non-personalized ads before loading;
`ad_personalization` remains denied. No custom ad units, placements or account Auto ads settings
were added or changed. Google's loader can create its own auxiliary no-slot elements.

## Verify manually in AdSense

For otttracker.in, choose **Meta tag** as the ownership verification method. Confirm that you have
placed the tag/code and click **Verify**. Alternatively choose **Ads.txt snippet** and confirm
publication. Both are supported by Google and publicly available without consent.

Do not rely on **AdSense code snippet** verification: the executable script intentionally waits
for advertising consent, which crawlers will not grant. It loads normally once a visitor explicitly
allows Advertising. The static meta tag is the reliable verification mechanism for this setup.

Ownership verification is separate from approval. No review was requested automatically. The
operator should complete any review step manually. This custom banner is not a certified Google
CMP; complete the applicable certified CMP setup before serving ads where Google requires it.

## Production validation

Public homepage, ads.txt, robots.txt and sitemap returned 200 without redirects for Googlebot,
Mediapartners-Google and AdsBot-Google user agents. No challenge/authentication/consent was needed.
The initial homepage HTML includes the publisher meta tag. ads.txt contains exactly:

```
google.com, pub-3451760985606446, DIRECT, f08c47fec0942fa0
```

Isolated browser testing confirmed the first-visit banner, Allow All, reopening/rejecting preferences,
one AdSense loader across SPA navigation, HTTP 200 for adsbygoogle.js and show_ads_impl.js, and GA4
collection HTTP 204. Rejection removes the AdSense loader on reload. No application ad slot rendered.

CSP permits only observed supporting ad-quality endpoints and frames. One exact Google-generated
style attribute is allowed via its SHA-256 hash with `style-src-attr 'unsafe-hashes'`; this does not
allow arbitrary inline styles or scripts. Existing GA4 allowances remain. The separately blocked
Cloudflare Insights beacon is unrelated and unchanged. Future ad formats may need their own CSP QA.

64 frontend tests and 15 SEO endpoint tests passed. Only the API (new environment values) and
frontend (build-time values and headers) were redeployed. No ports, data, DNS/tunnel or unrelated
containers were changed. Existing work remains uncommitted.

Reference: [Google's supported site verification methods](https://support.google.com/adsense/answer/7584263?hl=en).
