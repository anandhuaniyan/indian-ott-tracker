"""Provider-stack alias for the existing tightly budgeted Tavily discovery client."""

from app.services.ott_providers import TavilySearchProvider


class TavilyProvider(TavilySearchProvider):
    name = "tavily"
