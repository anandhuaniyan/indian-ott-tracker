from app.services.tmdb.client import TMDbClient


class TMDbOttService:

    def __init__(self):
        self.client = TMDbClient()

    def get_movie_watch_providers(self, tmdb_id: int, region: str = "IN") -> dict:
        """Fetch raw watch providers response for a movie filtered by region."""
        try:
            response = self.client.get(f"/movie/{tmdb_id}/watch/providers")
            results = response.get("results", {})
            return results.get(region, {})
        except Exception as e:
            print(f"Error fetching watch providers for tmdb_id {tmdb_id}: {e}")
            return {}

    def get_parsed_providers(self, tmdb_id: int, region: str = "IN") -> list[dict]:
        """Fetch and parse all providers into standardized dicts containing:
        provider_name, provider_logo, country, watch_type, watch_url, source_type ('tmdb')
        """
        raw = self.get_movie_watch_providers(tmdb_id, region)
        if not raw:
            return []

        watch_url = raw.get("link")
        parsed = []

        type_mapping = {
            "flatrate": "subscription",
            "free": "free",
            "rent": "rent",
            "buy": "buy",
            "ads": "ads",
        }

        for category, watch_type in type_mapping.items():
            providers = raw.get(category, [])
            for p in providers:
                name = p.get("provider_name")
                logo_path = p.get("logo_path")
                logo_url = f"https://image.tmdb.org/t/p/w500{logo_path}" if logo_path else None
                if name:
                    parsed.append({
                        "provider": name,
                        "provider_logo": logo_url,
                        "country": region,
                        "watch_type": watch_type,
                        "watch_url": watch_url,
                        "source_type": "TMDB",
                        "confidence": 75.0,
                    })

        return parsed
