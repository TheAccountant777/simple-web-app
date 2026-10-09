"""Fetch a web page and extract its readable text."""

from datetime import UTC, datetime
from typing import Literal

import trafilatura
from pydantic import BaseModel

from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import FetchError
from kenya_data_engine.http import fetch

MIN_CHARS = 200
JINA_BASE = "https://r.jina.ai/"


class PageText(BaseModel):
    url: str
    title: str | None
    text: str
    fetched_at: datetime
    via: Literal["direct", "jina"]


def _extract(html: str) -> tuple[str, str | None]:
    text = trafilatura.extract(html, include_tables=True) or ""
    meta = trafilatura.extract_metadata(html)
    return text.strip(), (meta.title if meta else None)


async def fetch_page(url: str, ctx: RunContext) -> PageText:
    ttl = ctx.config.cache_ttl_hours
    async with ctx.tracer.span("tools", "tool", "fetch_page"):
        text, title = "", None
        try:
            res = await fetch(url, client=ctx.http, cache=ctx.cache, ttl_hours=ttl, aia=ctx.tls)
            text, title = _extract(res.content.decode("utf-8", errors="replace"))
        except FetchError:
            pass  # fall through to the reader proxy
        if len(text) >= MIN_CHARS:
            return PageText(
                url=url, title=title, text=text, fetched_at=datetime.now(UTC), via="direct"
            )
        headers = {}
        if ctx.secrets.jina_api_key is not None:
            headers["Authorization"] = f"Bearer {ctx.secrets.jina_api_key.get_secret_value()}"
        res = await fetch(
            JINA_BASE + url, client=ctx.http, cache=ctx.cache, ttl_hours=ttl, headers=headers
        )
        body = res.content.decode("utf-8", errors="replace").strip()
        if len(body) < MIN_CHARS:
            raise FetchError(
                f"no readable text at {url}", hint="the page may need a browser or be paywalled"
            )
        return PageText(url=url, title=title, text=body, fetched_at=datetime.now(UTC), via="jina")
