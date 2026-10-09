"""Adapter contract plus the policy-checked fetch every adapter shares."""

from contextvars import ContextVar
from datetime import date
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel

from kenya_data_engine.context import RunContext
from kenya_data_engine.data.models import CheckReport, Observation
from kenya_data_engine.data.store import AddResult
from kenya_data_engine.http import FetchPolicy, FetchResult, fetch
from kenya_data_engine.tools.urlpolicy import Resolver

if TYPE_CHECKING:
    from kenya_data_engine.data.registry import CatalogEntry

# Tests inject a fake DNS here so check_url never touches the network.
DNS_RESOLVER: Resolver | None = None
# The probe sets a list here to learn the final URL of every page an adapter fetched.
FETCH_LOG: ContextVar[list[str] | None] = ContextVar("fetch_log", default=None)


class Discovered(BaseModel):
    url: str
    title: str | None = None
    published: date | None = None


class FetchOutcome(BaseModel):
    key: str
    discovered: int
    fetched: int
    added: AddResult | None
    report: CheckReport | None
    error: str | None


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
        resolve=DNS_RESOLVER,
    )


async def policy_fetch(
    url: str,
    ctx: RunContext,
    kind: Literal["page", "item"],
    headers: dict[str, str] | None = None,
) -> FetchResult:
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
