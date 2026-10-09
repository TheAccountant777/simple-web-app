"""Pluggable web search with ordered fallback."""

from typing import Protocol

import httpx
from pydantic import BaseModel

from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import ConfigError, SearchError
from kenya_data_engine.trace import Tracer

TIMEOUT_S = 20.0


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    content: str | None = None


class SearchProvider(Protocol):
    name: str

    async def search(
        self, query: str, n: int = 5, domains: list[str] | None = None
    ) -> list[SearchResult]: ...


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        self._key = api_key
        self._client = client

    async def search(
        self, query: str, n: int = 5, domains: list[str] | None = None
    ) -> list[SearchResult]:
        resp = await self._client.post(
            "https://api.tavily.com/search",
            json={"query": query, "max_results": n, "include_domains": domains},
            headers={"Authorization": f"Bearer {self._key}"},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        return [
            SearchResult(title=r["title"], url=r["url"], snippet=r.get("content", ""))
            for r in resp.json().get("results", [])
        ]


class SerperProvider:
    name = "serper"

    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        self._key = api_key
        self._client = client

    async def search(
        self, query: str, n: int = 5, domains: list[str] | None = None
    ) -> list[SearchResult]:
        q = query
        if domains:
            q += " (" + " OR ".join(f"site:{d}" for d in domains) + ")"
        resp = await self._client.post(
            "https://google.serper.dev/search",
            json={"q": q, "num": n},
            headers={"X-API-KEY": self._key},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        return [
            SearchResult(title=r["title"], url=r["link"], snippet=r.get("snippet", ""))
            for r in resp.json().get("organic", [])
        ]


class FallbackSearch:
    name = "fallback"

    def __init__(self, providers: list[SearchProvider], tracer: Tracer) -> None:
        self.providers = providers
        self._tracer = tracer

    async def search(
        self, query: str, n: int = 5, domains: list[str] | None = None
    ) -> list[SearchResult]:
        for provider in self.providers:
            try:
                async with self._tracer.span("tools", "tool", f"search.{provider.name}"):
                    return await provider.search(query, n, domains)
            except Exception:  # any failure moves on to the next provider
                continue
        raise SearchError("all search providers failed", hint="check keys with engine doctor")


def build_search(ctx: RunContext) -> FallbackSearch:
    keys = {
        "tavily": ctx.secrets.tavily_api_key,
        "serper": ctx.secrets.serper_api_key,
    }
    providers: list[SearchProvider] = []
    for name in ctx.config.search.providers:
        key = keys.get(name)
        if key is None:
            continue
        cls = TavilyProvider if name == "tavily" else SerperProvider
        providers.append(cls(key.get_secret_value(), ctx.http))
    if not providers:
        raise ConfigError("no search provider key configured", hint="run engine init")
    return FallbackSearch(providers, ctx.tracer)
