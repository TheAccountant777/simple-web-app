"""Cached, retrying HTTP fetch."""

import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

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
from kenya_data_engine.tls import AiaFixer, is_incomplete_chain
from kenya_data_engine.tools.urlpolicy import Resolver, check_url
from kenya_data_engine.trace import TraceEvent, Tracer

TIMEOUT_S = 20.0
# Browser-like: some Kenyan WAFs (CBK, KRA) reject obviously non-browser agents.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/124.0 Safari/537.36 kenya-data-engine/{__version__}"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
CHAIN_HINT = "this server sends an incomplete certificate chain"
# 0.5 s, 1 s, 2 s plus jitter; tests replace this with wait_none().
_WAIT: Any = wait_exponential(multiplier=0.5, exp_base=2) + wait_random(0, 0.25)


class FetchResult(BaseModel):
    url: str
    status: int
    content: bytes
    content_type: str
    from_cache: bool


class FetchPolicy(BaseModel):
    """Opt-in hardening: checked redirects (SSRF guard) and a body size cap."""

    max_bytes: int
    max_redirects: int = 5
    resolve: Any = None  # Resolver | None; tests inject a fake DNS


class _ServerError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"server error {status}")
        self.status = status


class _ChainError(Exception):
    """The server omitted its intermediate certificate; retrying as-is cannot help."""


_REDIRECTS = {301, 302, 303, 307, 308}
_WIRE_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}
_CREDENTIAL_HEADERS = {"authorization", "cookie"}


def _origin(url: str) -> tuple[str, str, int | None]:
    u = httpx.URL(url)
    return (u.scheme, u.host, u.port)  # port is None when default; scheme disambiguates


async def _get_checked(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], policy: FetchPolicy
) -> httpx.Response:
    """GET following redirects by hand: check every hop, cap the body size."""
    resolve: Resolver | None = policy.resolve
    current = url
    origin = _origin(url)
    send = headers
    for _ in range(policy.max_redirects + 1):
        await check_url(current, resolve)
        if _origin(current) != origin:  # never leak credentials across origins
            send = {k: v for k, v in headers.items() if k.lower() not in _CREDENTIAL_HEADERS}
        async with client.stream(
            "GET", current, headers=send, timeout=TIMEOUT_S, follow_redirects=False
        ) as resp:
            location = resp.headers.get("location")
            if resp.status_code in _REDIRECTS and location:
                current = str(resp.url.join(location))
                continue
            if resp.status_code >= 500:
                raise _ServerError(resp.status_code)
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > policy.max_bytes:
                raise FetchError(f"response too large for {current} (> {policy.max_bytes} bytes)")
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body.extend(chunk)
                if len(body) > policy.max_bytes:
                    raise FetchError(
                        f"response too large for {current} (> {policy.max_bytes} bytes)"
                    )
            # aiter_bytes already decoded the body: drop the headers that describe the wire form.
            kept = [(k, v) for k, v in resp.headers.multi_items() if k.lower() not in _WIRE_HEADERS]
            return httpx.Response(
                resp.status_code, headers=kept, content=bytes(body), request=resp.request
            )
    raise FetchError(f"too many redirects (> {policy.max_redirects}) for {url}")


async def _get(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    policy: FetchPolicy | None = None,
) -> httpx.Response:
    try:
        if policy is not None:
            return await _get_checked(client, url, headers, policy)
        resp = await client.get(url, headers=headers, timeout=TIMEOUT_S, follow_redirects=True)
    except httpx.ConnectError as exc:
        if is_incomplete_chain(exc):
            raise _ChainError(str(exc)) from exc
        raise
    if resp.status_code >= 500:
        raise _ServerError(resp.status_code)
    return resp


async def _retry_with_intermediate(
    url: str,
    send: dict[str, str],
    aia: AiaFixer | None,
    original: str,
    policy: FetchPolicy | None = None,
) -> httpx.Response:
    """One retry with the missing intermediate added to the trust store; else FetchError."""
    failure = f"transport error for {url}: {original}"
    if aia is None:
        raise FetchError(failure, hint=CHAIN_HINT)
    try:
        return await _get(await aia.client_for(url), url, send, policy)
    except Exception as exc:  # any repair failure: surface the original problem
        raise FetchError(
            failure, hint=f"{CHAIN_HINT}; fetching the missing certificate failed ({exc})"
        ) from exc


# Per-source request tally: radar sets a fresh dict around each adapter (each runs in its own
# asyncio task, so concurrent adapters never mix); `fetch` adds one call (and one hit) to it.
HTTP_TALLY: ContextVar[dict[str, int] | None] = ContextVar("http_tally", default=None)


async def fetch(
    url: str,
    *,
    client: httpx.AsyncClient,
    cache: Cache,
    ttl_hours: float,
    headers: dict[str, str] | None = None,
    aia: AiaFixer | None = None,
    tracer: Tracer | None = None,
    policy: FetchPolicy | None = None,
) -> FetchResult:
    """Fetch `url`; with a tracer, record one `http` event per call."""
    info: dict[str, Any] = {"status": None, "from_cache": False, "bytes": 0, "attempts": 0}
    start = time.perf_counter()
    error: str | None = None
    try:
        return await _fetch(url, client, cache, ttl_hours, headers, aia, info, policy)
    except BaseException as exc:
        error = str(exc) or type(exc).__name__
        raise
    finally:
        tally = HTTP_TALLY.get()
        if tally is not None:
            tally["http"] = tally.get("http", 0) + 1
            tally["cache_hits"] = tally.get("cache_hits", 0) + bool(info["from_cache"])
        if tracer is not None:
            tracer.record(
                TraceEvent(
                    ts=datetime.now(UTC),
                    run_id=tracer.run_id,
                    stage="http",
                    kind="http",
                    name=urlsplit(url).hostname or url,
                    status="ok" if error is None else "error",
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    error=error,
                    attrs={"url": tracer.redact(url), **info},
                )
            )


async def _fetch(
    url: str,
    client: httpx.AsyncClient,
    cache: Cache,
    ttl_hours: float,
    headers: dict[str, str] | None,
    aia: AiaFixer | None,
    info: dict[str, Any],
    policy: FetchPolicy | None = None,
) -> FetchResult:
    key = normalize_url(url)
    hit = cache.get(key)
    if hit is not None:
        if policy is not None:
            await check_url(url, policy.resolve)
            if len(hit.content) > policy.max_bytes:
                raise FetchError(
                    f"cached response too large for {url} (> {policy.max_bytes} bytes)"
                )
        info.update(status=200, from_cache=True, bytes=len(hit.content))
        return FetchResult(
            url=url, status=200, content=hit.content, content_type=hit.content_type, from_cache=True
        )
    send = {**DEFAULT_HEADERS, **(headers or {})}
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=_WAIT,
            retry=retry_if_exception_type((httpx.TransportError, _ServerError)),
            reraise=True,
        ):
            with attempt:
                info["attempts"] += 1
                resp = await _get(client, url, send, policy)
    except _ServerError as exc:
        raise FetchError(
            f"{exc.status} for {url}", hint="the site may be down; retry later"
        ) from exc
    except _ChainError as exc:
        resp = await _retry_with_intermediate(url, send, aia, str(exc), policy)
    except httpx.TransportError as exc:
        raise FetchError(
            f"transport error for {url}: {exc}", hint="check your network connection"
        ) from exc
    info.update(status=resp.status_code, bytes=len(resp.content))
    if not resp.is_success:
        raise FetchError(f"{resp.status_code} for {url}")
    ctype = resp.headers.get("content-type", "")
    cache.put(key, resp.content, ctype, ttl_hours)
    final = str(resp.url) if policy is not None else url
    return FetchResult(
        url=final,
        status=resp.status_code,
        content=resp.content,
        content_type=ctype,
        from_cache=False,
    )
