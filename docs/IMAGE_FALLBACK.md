# Image fallback

Movie image records preserve source, remote URL, local cache path and verification time. The frontend uses a local placeholder only after an image fails to load. Recovery workers should try configured, permitted providers in priority order, record successful source and failure reasons, use backoff for repeated failures, then create `data_quality_issues` for unresolved artwork.
