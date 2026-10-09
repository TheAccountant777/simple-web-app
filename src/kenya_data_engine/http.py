"""Cached, retrying HTTP fetch."""

from typing import Any

import httpx
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from kenya_data_engine import __version__
from kenya_data_engine.cache import Cache
from kenya_data_engine.errors import FetchError
from kenya_data_engine.models import normalize_url

TIMEOUT_S = 20.0
USER_AGENT = f"kenya-data-engine/{__version__} (+research)"
# 0.5 s, 1 s, 2 s plus jitter; tests replace this with wait_none().
_WAIT: Any = wait_exponential(multiplier=0.5, exp_base=2) + wait_random(0, 0.25)


class FetchResult(BaseModel):
    url: str
    status: int
    content: bytes
    content_type: str
    from_cache: bool


class _ServerError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"server error {status}")
        self.status = status


async def _get(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> httpx.Response:
    resp = await client.get(url, headers=headers, timeout=TIMEOUT_S, follow_redirects=True)
    if resp.status_code >= 500:
        raise _ServerError(resp.status_code)
    return resp


async def fetch(
    url: str,
    *,
    client: httpx.AsyncClient,
    cache: Cache,
    ttl_hours: float,
    headers: dict[str, str] | None = None,
) -> FetchResult:
    key = normalize_url(url)
    hit = cache.get(key)
    if hit is not None:
        return FetchResult(
            url=url, status=200, content=hit.content, content_type=hit.content_type, from_cache=True
        )
    send = {"User-Agent": USER_AGENT, **(headers or {})}
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=_WAIT,
            retry=retry_if_exception_type((httpx.TransportError, _ServerError)),
            reraise=True,
        ):
            with attempt:
                resp = await _get(client, url, send)
    except _ServerError as exc:
        raise FetchError(
            f"{exc.status} for {url}", hint="the site may be down; retry later"
        ) from exc
    except httpx.TransportError as exc:
        raise FetchError(
            f"transport error for {url}: {exc}", hint="check your network connection"
        ) from exc
    if not resp.is_success:
        raise FetchError(f"{resp.status_code} for {url}")
    ctype = resp.headers.get("content-type", "")
    cache.put(key, resp.content, ctype, ttl_hours)
    return FetchResult(
        url=url, status=resp.status_code, content=resp.content, content_type=ctype, from_cache=False
    )
