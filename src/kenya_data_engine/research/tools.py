"""Tools the research agents call. Each returns a string; the model never sees an exception.

URL allowlist: a URL an agent passes in must have been returned by a tool earlier in this dossier
(`book.seen_urls`), or belong to a registry entry or to source memory.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol
from urllib.parse import urljoin

from pydantic_ai import RunContext as PaiRunContext
from selectolax.lexbor import LexborHTMLParser

from kenya_data_engine.context import RunContext
from kenya_data_engine.data.adapters.base import policy_fetch, robots_allowed
from kenya_data_engine.data.extract import Locator, extract_tables
from kenya_data_engine.data.registry import CatalogEntry
from kenya_data_engine.errors import BudgetExceeded
from kenya_data_engine.models import normalize_url
from kenya_data_engine.research.budget import Ledger
from kenya_data_engine.research.evidence import EvidenceBook
from kenya_data_engine.research.models import DataNeed, DataSourceSpec
from kenya_data_engine.tools.search import FallbackSearch

READ_CHARS = 8_000
MAX_LINKS = 40
PREVIEW_ROWS = 5
_STOP_WORDS = 2  # query words of this length or shorter are ignored


class SourceMemoryLike(Protocol):
    """What the tools need from source memory (the real store arrives in a later task)."""

    def lookup(self, need: DataNeed, today: date) -> list[DataSourceSpec]:
        """Remembered source specs that worked for a similar need."""
        ...

    def search(self, query: str) -> list[tuple[str, DataSourceSpec]]:
        """(memory key, spec) pairs matching a free-text query."""
        ...


@dataclass
class ResearchDeps:
    ctx: RunContext
    ledger: Ledger
    group: str
    book: EvidenceBook
    catalog: dict[str, CatalogEntry]
    memory: SourceMemoryLike
    search: FallbackSearch
    log: list[str] = field(default_factory=list)  # tool-call lines for verification.md
    memory_urls: set[str] = field(default_factory=set)  # urls of memory specs seen this dossier
    _registry_cache: set[str] | None = field(default=None, repr=False)


Ctx = PaiRunContext[ResearchDeps]


def _note(deps: ResearchDeps, line: str) -> None:
    deps.log.append(deps.ctx.tracer.redact(line))


def _err(deps: ResearchDeps, tool: str, exc: Exception) -> str:
    msg = deps.ctx.tracer.redact(str(exc) or type(exc).__name__)
    _note(deps, f"{tool}: error {msg}")
    return f"error: {msg}"


def _registry_urls(deps: ResearchDeps) -> set[str]:
    if deps._registry_cache is not None:
        return deps._registry_cache
    out: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            out.add(normalize_url(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for entry in deps.catalog.values():
        if entry.enabled:
            walk(entry.params)
    deps._registry_cache = out
    return out


def _allowed(deps: ResearchDeps, url: str) -> bool:
    if url in deps.book.seen_urls:
        return True
    norm = normalize_url(url)
    if any(normalize_url(u) == norm for u in deps.book.seen_urls):
        return True
    return norm in _registry_urls(deps) or url in deps.memory_urls or norm in deps.memory_urls


_ROBOTS = "error: disallowed by robots.txt"


async def _robots_blocked(deps: ResearchDeps, url: str) -> bool:
    if normalize_url(url) in _registry_urls(deps):
        return False  # registry sources are polled by the data layer under its own rules
    return not await robots_allowed(url, deps.ctx)


def _seen(deps: ResearchDeps, *urls: str) -> None:
    deps.book.seen_urls.update(deps.ctx.tracer.redact(u) for u in urls)


_UNKNOWN_URL = "error: unknown url — use a url from search results"


async def web_search(ctx: Ctx, query: str, domains: list[str] | None = None) -> str:
    """Search the web. Returns numbered results: title, url, snippet."""
    deps = ctx.deps
    try:
        results = await deps.search.search(query, n=5, domains=domains or None)
    except BudgetExceeded:
        _note(deps, f"web_search {query!r}: budget exhausted")
        return "budget exhausted: stop searching"
    except Exception as exc:
        return _err(deps, "web_search", exc)
    _seen(deps, *(r.url for r in results))
    _note(deps, f"web_search {query!r}: {len(results)} results")
    if not results:
        return "no results"
    return "\n".join(
        f"[{i}] {r.title} — {r.url} — {' '.join(r.snippet.split())[:300]}"
        for i, r in enumerate(results, start=1)
    )


async def read_page(ctx: Ctx, url: str, focus: str) -> str:
    """Read an HTML page or PDF. Returns an <evidence> packet with passages relevant to `focus`."""
    deps = ctx.deps
    if not _allowed(deps, url):
        _note(deps, f"read_page {url}: rejected, unknown url")
        return _UNKNOWN_URL
    try:
        if await _robots_blocked(deps, url):
            _note(deps, f"read_page {url}: disallowed by robots.txt")
            return _ROBOTS
        ev = await deps.book.add_url(url, deps.ctx)
    except Exception as exc:
        return _err(deps, "read_page", exc)
    _note(deps, f"read_page {url}: {ev.label} tier {ev.tier}")
    return deps.book.packet([ev.label], focus, READ_CHARS)


async def list_links(ctx: Ctx, url: str, contains: str = "") -> str:
    """List links on a page (at most 40), optionally only those containing `contains`."""
    deps = ctx.deps
    if not _allowed(deps, url):
        _note(deps, f"list_links {url}: rejected, unknown url")
        return _UNKNOWN_URL
    try:
        if await _robots_blocked(deps, url):
            _note(deps, f"list_links {url}: disallowed by robots.txt")
            return _ROBOTS
        res = await policy_fetch(url, deps.ctx, "page")
        _seen(deps, res.url)
        tree = LexborHTMLParser(res.content.decode("utf-8", errors="replace"))
    except Exception as exc:
        return _err(deps, "list_links", exc)
    needle = contains.lower()
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = urljoin(res.url, (a.attributes.get("href") or "").strip())
        text = " ".join((a.text() or "").split())
        if not href.startswith(("http://", "https://")) or href in seen:
            continue
        if needle and needle not in href.lower() and needle not in text.lower():
            continue
        seen.add(href)
        found.append((text[:100], href))
        if len(found) >= MAX_LINKS:
            break
    _seen(deps, *(h for _, h in found))
    _note(deps, f"list_links {url}: {len(found)} links")
    if not found:
        return "no links found"
    return "\n".join(f"[{i}] {t or '(no text)'} — {h}" for i, (t, h) in enumerate(found, start=1))


async def preview_table(ctx: Ctx, url: str, page: int | None = None, table_index: int = 0) -> str:
    """Show the header and first rows of a table, to choose a locator (not to read values)."""
    deps = ctx.deps
    if not _allowed(deps, url):
        _note(deps, f"preview_table {url}: rejected, unknown url")
        return _UNKNOWN_URL
    try:
        if await _robots_blocked(deps, url):
            _note(deps, f"preview_table {url}: disallowed by robots.txt")
            return _ROBOTS
        res = await policy_fetch(url, deps.ctx, "item")
        _seen(deps, res.url)
        tables = await extract_tables(
            res.content, Locator(pages=[page] if page else []), deps.ctx.config.data
        )
    except Exception as exc:
        return _err(deps, "preview_table", exc)
    if not tables:
        _note(deps, f"preview_table {url}: no tables")
        return "no tables found"
    if not 0 <= table_index < len(tables):
        _note(deps, f"preview_table {url}: table_index {table_index} out of range")
        return f"error: table_index {table_index} out of range; {len(tables)} tables found"
    t = tables[table_index]
    lines = [" | ".join(t.header), *(" | ".join(row) for row in t.rows[:PREVIEW_ROWS])]
    _note(deps, f"preview_table {url}: table {table_index} of {len(tables)}")
    return f"table {table_index} of {len(tables)} ({len(t.rows)} rows)\n" + "\n".join(lines)


def registry_lookup(ctx: Ctx, query: str) -> str:
    """Find enabled registry series whose key or title matches the query words."""
    deps = ctx.deps
    try:
        words = [w for w in query.lower().split() if len(w) > _STOP_WORDS]
        hits = [
            f"{e.key} — {e.title} — {e.publisher} — tier {e.tier}"
            for e in deps.catalog.values()
            if e.enabled and any(w in f"{e.key} {e.title}".lower() for w in words)
        ]
    except Exception as exc:
        return _err(deps, "registry_lookup", exc)
    _note(deps, f"registry_lookup {query!r}: {len(hits)} hits")
    return "\n".join(hits) if hits else "no registry series match"


def memory_lookup(ctx: Ctx, query: str) -> str:
    """Remembered sources that worked for similar needs."""
    deps = ctx.deps
    try:
        pairs = deps.memory.search(query)
    except Exception as exc:
        return _err(deps, "memory_lookup", exc)
    lines = []
    for key, spec in pairs:
        if spec.url:
            deps.memory_urls.add(spec.url)
            deps.memory_urls.add(normalize_url(spec.url))
        lines.append(f"{key} — {spec.publisher} — {spec.url}")
    _note(deps, f"memory_lookup {query!r}: {len(lines)} hits")
    return "\n".join(lines) if lines else "no remembered sources"
