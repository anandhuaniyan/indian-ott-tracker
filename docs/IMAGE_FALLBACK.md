# Image fallback

`ImageFallbackService` covers movie posters, backdrops and logos plus person profiles. It derives `HEALTHY`, `MISSING` and `BROKEN` using file existence, non-zero size and Pillow verification. Recovery outcomes add `RETRYING`, `RECOVERED`, `UNRESOLVED` semantics through returned status and deduplicated health issues.

The lawful chain is: validated local media; current TMDB image; stored `MovieImage` candidates ordered TMDB, Fanart and other configured permitted sources; the existing poster provider chain; then the frontend placeholder. Google Images and consumer search-page scraping are not used.

Movie and person cursors are stored separately in `operation_states`. Each bounded scan attempts all three movie image kinds and profiles, advances the cursor and resets it only after reaching the end, so scheduled runs cover every movie/person. Admin image health shows counts and unresolved/recovered records and permits an explicit retry.
