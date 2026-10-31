"""Everything a run needs from the outside world, injected so tests and the benchmark can swap it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.config import Settings, get_settings
from app.llm.gateway import ModelGateway, build_providers
from app.research.web import FetchedPage, HttpFetcher, SearchProvider, TavilySearch, WikipediaSearch
from app.services.observability import record_call_to_db, under_daily_limit


class Fetcher(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...


@dataclass
class ResearchEnv:
    gateway: ModelGateway
    search: SearchProvider
    fetcher: Fetcher
    settings: Settings


def build_search(settings: Settings) -> SearchProvider:
    if settings.resolved_search_provider == "tavily":
        if not settings.tavily_api_key:
            raise RuntimeError("SEARCH_PROVIDER=tavily but TAVILY_API_KEY is not set.")
        return TavilySearch(settings.tavily_api_key)
    return WikipediaSearch(user_agent=settings.http_user_agent)


def build_default_env(settings: Settings | None = None) -> ResearchEnv:
    settings = settings or get_settings()
    gateway = ModelGateway(build_providers(settings), record_call_to_db, max_attempts=settings.model_max_attempts,
                           allow_call=under_daily_limit)
    fetcher = HttpFetcher(timeout=settings.fetch_timeout_seconds, max_bytes=settings.fetch_max_bytes,
                          allow_private=settings.allow_private_fetch, user_agent=settings.http_user_agent)
    return ResearchEnv(gateway, build_search(settings), fetcher, settings)
