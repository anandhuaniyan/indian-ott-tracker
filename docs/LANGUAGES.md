# Language classification

OTTTracker classifies a movie by TMDB's `original_language`. Spoken languages,
production countries, translated titles, cast, and release territories do not
change that classification.

The canonical configuration is `app/services/languages.py`. Public navigation,
filters, ingestion, admin reporting, and display names derive from that file.
The primary public groups are:

- Indian: `ml`, `ta`, `te`, `hi`, `kn`
- International: `en`, `ko`, `ja`, `zh`, `es`, `fr`

Existing supported “More” languages remain public, but do not become primary
homepage sections. Unknown and unsupported codes stay visible to administrators
and are not silently assigned to another language.

## Normalization policy

Whitespace and case are normalized. Only unambiguous ISO aliases are folded:
`eng` to `en`, `jpn` to `ja`, `kor` to `ko`, `fra`/`fre` to `fr`, and `spa` to
`es`. Normalization is idempotent and changes only `movies.original_language`.

Chinese-language codes are deliberately conservative:

- `zh` remains `zh` and is displayed as Chinese.
- `cn` remains `cn`; it is commonly a country code and is not treated as a safe
  language alias.
- `yue` remains `yue` (Cantonese).
- `cmn` remains `cmn` (Mandarin Chinese).

This avoids assigning Cantonese, Mandarin, or malformed country-code data to the
broad `zh` bucket without authoritative metadata. An administrator may correct
such records after verifying the source.

## Audit and repair

Run a read-only audit with:

```powershell
docker compose exec -T api python -m scripts.language_audit
```

After taking a verified PostgreSQL backup, normalize safe aliases with:

```powershell
docker compose exec -T api python -m scripts.language_audit --apply
```

The authenticated `/api/v1/admin/language-health` endpoint and Admin dashboard
report primary-language totals, nulls, unknown codes, unsupported codes, and
normalization mismatches. The operation is safe to repeat.

## Catalogue ingestion

The catalogue-only TMDB backfill covers every year from 2015 through the
current date for all eleven primary language codes. It classifies solely by
TMDB `original_language`, rejects adult/video entries and invalid release
dates, and checks both TMDB and IMDb identifiers before inserting a movie.

Progress is stored per language in PostgreSQL as
`movies.catalogue_backfill.<code>`. Each successful TMDB page records its year
and page cursor, so a stopped process resumes without starting over. These
states, including the current language/year/page, are visible in Admin → Jobs.

The relevance thresholds are deliberately explicit: Indian languages have no
vote-count floor; English requires 20 TMDB votes; Korean, Japanese, Chinese,
Spanish, and French require 5. The importer enriches normal movie metadata but
does not write OTT availability, evidence, provider mappings, or OTT research
queues.

Twice-daily regular discovery uses the same eleven canonical language codes
and scans at least 365 days into the future. This continuously picks up newly
announced films while preserving identifier-first deduplication.
