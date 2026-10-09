"""Adapter contract plus the policy-checked fetch every adapter shares."""

import asyncio
from contextvars import ContextVar
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from pydantic import BaseModel

from kenya_data_engine.context import RunContext
from kenya_data_engine.data.models import CheckReport, Observation
from kenya_data_engine.data.store import AddResult
from kenya_data_engine.http import USER_AGENT, FetchPolicy, FetchResult, fetch

if TYPE_CHECKING:
    from kenya_data_engine.data.registry import CatalogEntry

# The probe sets a list here to learn the final URL of every page an adapter fetched.
FETCH_LOG: ContextVar[list[str] | None] = ContextVar("fetch_log", default=None)


class Discovered(BaseModel):
    url: str
    title: str | None = None
    published: date | None = None
    # Set by fetch_series once the item is fetched, for provenance: where the redirects ended
    # and when the bytes were fetched (the cache entry's time on a hit).
    final_url: str | None = None
    retrieved_at: datetime | None = None


class ItemOutcome(BaseModel):
    """What happened to one discovered item (a release, a file, an API body)."""

    url: str
    final_url: str | None = None
    sha256: str | None = None  # None when nothing was downloaded
    status: Literal["added", "unchanged", "quarantined", "error"]
    report: CheckReport | None = None
    error: str | None = None


class FetchOutcome(BaseModel):
    key: str
    discovered: int
    fetched: int
    added: AddResult | None
    report: CheckReport | None
    error: str | None
    items: list[ItemOutcome] = []


class Adapter(Protocol):
    kind: str

    async def discover(self, entry: "CatalogEntry", ctx: RunContext) -> list[Discovered]: ...

    async def observations(
        self,
        entry: "CatalogEntry",
        item: Discovered,
        content: bytes,
        sha: str,
        ctx: RunContext,
    ) -> list[Observation]: ...


def fetch_policy(ctx: RunContext, kind: Literal["page", "item"]) -> FetchPolicy:
    """Listing pages get the html cap; items (PDF, sheets, API bodies) the largest, the pdf cap."""
    caps = ctx.config.data.max_bytes
    return FetchPolicy(
        max_bytes=caps["html"] if kind == "page" else max(caps["pdf"], caps["sheet"]),
        max_redirects=ctx.config.data.max_redirects,
        resolve=ctx.resolver,
    )


def _slot(ctx: RunContext, url: str) -> asyncio.Semaphore:
    host = (urlsplit(url).hostname or "").lower()
    slot = ctx.domain_slots.get(host)
    if slot is None:
        slot = ctx.domain_slots[host] = asyncio.Semaphore(
            max(1, ctx.config.data.per_domain_concurrency)
        )
    return slot


async def policy_fetch(
    url: str,
    ctx: RunContext,
    kind: Literal["page", "item"],
    headers: dict[str, str] | None = None,
) -> FetchResult:
    """Policy-checked fetch; at most `data.per_domain_concurrency` requests per host at once."""
    async with _slot(ctx, url):
        res = await fetch(
            url,
            client=ctx.http,
            cache=ctx.cache,
            ttl_hours=ctx.config.cache_ttl_hours,
            headers=headers,
            aia=ctx.tls,
            tracer=ctx.tracer,
            policy=fetch_policy(ctx, kind),
        )
    log = FETCH_LOG.get()
    if log is not None:
        log.append(res.url)
    return res


async def robots_allowed(url: str, ctx: RunContext) -> bool:
    """May our user agent fetch `url` under the host's robots.txt? (R14)

    robots.txt is fetched once per host (through the same policy fetch) and kept on the context.
    A missing, unreachable or broken robots.txt means allowed.
    """
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    if origin not in ctx.robots:
        parser: RobotFileParser | None = None
        try:
            res = await policy_fetch(f"{origin}/robots.txt", ctx, "page")
            parser = RobotFileParser()
            parser.parse(res.content.decode("utf-8", errors="replace").splitlines())
        except Exception:  # 404, 5xx, blocked, unreadable: no usable rules
            parser = None
        ctx.robots[origin] = parser
    cached: RobotFileParser | None = ctx.robots[origin]
    # robotparser matches on the product token ("kenya-data-engine"), not the whole header.
    return True if cached is None else cached.can_fetch(USER_AGENT.rsplit(" ", 1)[-1], url)
