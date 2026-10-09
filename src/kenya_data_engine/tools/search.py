"""Pluggable web search with ordered fallback."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel

from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import ConfigError, SearchError
from kenya_data_engine.research.budget import Ledger
from kenya_data_engine.trace import Tracer

TIMEOUT_S = 20.0

Depth = Literal["basic", "advanced"]


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    content: str | None = None


class SearchProvider(Protocol):
    name: str

    async def search(
        self,
        query: str,
        n: int = 5,
        domains: list[str] | None = None,
        *,
        depth: Depth = "basic",
    ) -> list[SearchResult]: ...


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        self._key = api_key
        self._client = client

    async def search(
        self,
        query: str,
        n: int = 5,
        domains: list[str] | None = None,
        *,
        depth: Depth = "basic",
    ) -> list[SearchResult]:
        body: dict[str, object] = {"query": query, "max_results": n, "search_depth": depth}
        if domains:
            body["include_domains"] = domains
        resp = await self._client.post(
            "https://api.tavily.com/search",
            json=body,
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
        self,
        query: str,
        n: int = 5,
        domains: list[str] | None = None,
        *,
        depth: Depth = "basic",
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


class SearchUsage:
    """Search credits used per provider and calendar month (UTC), kept in the engine DB."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS search_usage ("
                "provider TEXT, month TEXT, credits INT, PRIMARY KEY(provider, month))"
            )

    @staticmethod
    def _month() -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    def add(self, provider: str, credits: int) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO search_usage VALUES (?, ?, ?) "
                "ON CONFLICT(provider, month) DO UPDATE SET credits = credits + excluded.credits",
                (provider, self._month(), credits),
            )

    def month_total(self, provider: str) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT credits FROM search_usage WHERE provider = ? AND month = ?",
                (provider, self._month()),
            ).fetchone()
        return int(row[0]) if row else 0


class FallbackSearch:
    name = "fallback"

    def __init__(
        self,
        providers: list[SearchProvider],
        tracer: Tracer,
        *,
        ledger: Ledger | None = None,
        group: str = "scouts",
        usage: SearchUsage | None = None,
        monthly_limit: int | None = None,
    ) -> None:
        self.providers = providers
        self._tracer = tracer
        self._ledger = ledger
        self._group = group
        self._usage = usage
        self._monthly_limit = monthly_limit

    async def search(
        self,
        query: str,
        n: int = 5,
        domains: list[str] | None = None,
        *,
        depth: Depth = "basic",
    ) -> list[SearchResult]:
        reasons: list[str] = []
        last: Exception | None = None
        credits = 1 if depth == "basic" else 2
        for provider in self.providers:
            if (
                self._usage is not None
                and self._monthly_limit is not None
                and self._usage.month_total(provider.name) + credits > self._monthly_limit
            ):
                reasons.append(f"{provider.name}: monthly credit limit reached")
                continue
            if self._ledger is not None:  # BudgetExceeded is final: no provider is tried
                self._ledger.charge_credits(self._group, credits)
            if self._usage is not None:
                self._usage.add(provider.name, credits)
            try:
                async with self._tracer.span("tools", "tool", f"search.{provider.name}") as attrs:
                    attrs["credits"] = credits
                    return await provider.search(query, n, domains, depth=depth)
            except Exception as exc:  # any failure moves on to the next provider
                last = exc
                reasons.append(f"{provider.name}: {self._reason(exc)}")
        raise SearchError(
            f"all search providers failed ({'; '.join(reasons)})",
            hint="check keys with engine doctor",
        ) from last

    def _reason(self, exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return str(exc.response.status_code)
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        return self._tracer.redact(str(exc) or type(exc).__name__)


def build_search(
    ctx: RunContext, *, ledger: Ledger | None = None, group: str = "scouts"
) -> FallbackSearch:
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
    if ledger is None:
        return FallbackSearch(providers, ctx.tracer)
    return FallbackSearch(
        providers,
        ctx.tracer,
        ledger=ledger,
        group=group,
        usage=SearchUsage(ctx.home.db_path),
        monthly_limit=ctx.config.research.monthly_search_credits,
    )
