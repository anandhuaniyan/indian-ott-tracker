# OTT research

`ott_evidence` records each research result with source, confidence, status, retry timestamps and notes. A worker should enqueue titles without confirmed availability, use only a configured lawful research provider, and preserve high-confidence confirmation when weaker evidence arrives. Conflicting evidence must be marked `CONFLICTING` for review rather than auto-published.

This repository intentionally contains no Google scraping implementation. Provider requests, rate limits and source terms must be configured by the deployment operator.
