# AdSense Setup

AdSense is fully optional and the codebase is already prepared: consent-gated loader, reserved ad slots,
and an `ads.txt` endpoint. All that is required is your approved identifiers.

## 1. Create the AdSense account

1. Apply at https://adsense.google.com/start.
2. During sign-up you will connect your site (via Google Search Console — see `GOOGLE_SEARCH_CONSOLE.md`).
3. Approval is **not guaranteed** and granted by Google, not by this codebase.

## 2. Get your identifiers

Once approved you will have:

| Identifier | Example | Used for |
| --- | --- | --- |
| Publisher ID | `pub-0000000000000000` | the `ads.txt` record (server-side) |
| Client ID | `ca-pub-0000000000000000` | the consent-gated loader + ad units (frontend) |
| Slot ID (optional) | `0000000000` | a reusable responsive slot for placements |

## 3. Configure

Add to the environment at build time; never commit real values:

- `ADSENSE_PUBLISHER_ID=pub-...` → backend serves the official record at `https://otttracker.in/ads.txt`:
  `google.com, pub-..., DIRECT, f08c47fec0942fa0`
- `VITE_ADSENSE_CLIENT_ID=ca-pub-...` → enables the script and ad units
- `VITE_ADSENSE_SLOT_ID=...` → binds the shared responsive slot used by placements

Without these, `ads.txt` is empty and no ad element or script is rendered anywhere.

## 4. Where ads display today

`AdSlot` components already render on:

- Home page
- Movie detail page
- Search results page (below the pagination, search mode only)

Each is a reserved `<ins>` with at least 90px of layout space, so they cause no layout shift when they load.
Placements only render when `VITE_ADSENSE_CLIENT_ID`, `VITE_ADSENSE_SLOT_ID` and advertising consent all
exist; `frontend/src/components/AdSlot.jsx` shows the exact gating.

## 5. Launch checklist

1. Confirm AdSense shows your site as approved.
2. Set the three variables and rebuild the frontend + restart the API.
3. Verify `https://otttracker.in/ads.txt` now contains your publisher record.
4. In AdSense, confirm **Allow ads on this site** and that your reviews (site behaviour) are complete.
5. Accept advertising consent once in a clean browser and confirm a test ad renders (`data-testid` is
   `aria-label="Advertisement"`).
6. Keep placements consistent with current AdSense policies; this project uses unobtrusive,
   pagination-following placements.