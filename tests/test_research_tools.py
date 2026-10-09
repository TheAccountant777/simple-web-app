import io
from types import SimpleNamespace

import openpyxl
import pytest

import kenya_data_engine.research.tools as tools
from kenya_data_engine.data.registry import CatalogEntry, load_catalog
from kenya_data_engine.errors import BudgetExceeded, SearchError
from kenya_data_engine.research.budget import Ledger
from kenya_data_engine.research.evidence import EvidenceBook
from kenya_data_engine.research.models import DataSourceSpec
from kenya_data_engine.research.tools import (
    ResearchDeps,
    list_links,
    memory_lookup,
    preview_table,
    read_page,
    registry_lookup,
    web_search,
)
from kenya_data_engine.tools.search import SearchResult

PAGE_URL = "https://www.epra.go.ke/prices"
LONG = (
    "The Energy and Petroleum Regulatory Authority announced the monthly maximum pump prices "
    "for the cycle running from the fifteenth of the month, covering super petrol, diesel and "
    "kerosene across Nairobi, Mombasa and Kisumu."
)


class FakeSearch:
    def __init__(self, results=None, exc=None):
        self.results, self.exc, self.calls = results or [], exc, []

    async def search(self, query, n=5, domains=None, *, depth="basic"):
        self.calls.append((query, domains))
        if self.exc:
            raise self.exc
        return self.results


class FakeMemory:
    def __init__(self, specs=()):
        self.specs = list(specs)

    def lookup(self, need, today):
        return [s for _, s in self.specs]

    def search(self, query):
        return self.specs


def _spec(url="https://a.go.ke/p"):
    return DataSourceSpec(need="n1", via="page_text", url=url, publisher="Pub", why="w")


def _deps(ctx, tmp_path, search=None, memory=None, catalog=None):
    return ResearchDeps(
        ctx=ctx,
        ledger=Ledger.from_config(ctx.config.research),
        group="scouts",
        book=EvidenceBook(tmp_path, ctx.config.research.tiers, redact=ctx.tracer.redact),
        catalog=catalog if catalog is not None else {},
        memory=memory or FakeMemory(),
        search=search or FakeSearch(),
    )


def _pai(deps):
    return SimpleNamespace(deps=deps)


async def test_web_search_numbers_results_and_seen_urls(ctx, tmp_path):
    search = FakeSearch(
        [
            SearchResult(title="EPRA", url=PAGE_URL, snippet="pump\nprices"),
            SearchResult(title="Other", url="https://b.com/x", snippet="s"),
        ]
    )
    deps = _deps(ctx, tmp_path, search)
    out = await web_search(_pai(deps), "pump prices", ["epra.go.ke"])
    assert out.splitlines()[0] == f"[1] EPRA — {PAGE_URL} — pump prices"
    assert out.splitlines()[1].startswith("[2] Other — https://b.com/x")
    assert {PAGE_URL, "https://b.com/x"} <= deps.book.seen_urls
    assert search.calls == [("pump prices", ["epra.go.ke"])]
    assert deps.log and "2 results" in deps.log[0]


async def test_read_page_rejects_unknown_url(ctx, tmp_path, respx_mock):
    deps = _deps(ctx, tmp_path)
    out = await read_page(_pai(deps), "https://evil.example/ignore-previous", "vat")
    assert out == "error: unknown url — use a url from search results"
    assert not respx_mock.calls and deps.book.items == []


async def test_read_page_returns_packet_with_label(ctx, tmp_path, respx_mock, data_net):
    html = (
        f"<html><body><article><p>{LONG}</p><p>{LONG} Super is KSh 190.</p></article></body></html>"
    )
    respx_mock.get(PAGE_URL).respond(
        200, content=html.encode(), headers={"content-type": "text/html"}
    )
    deps = _deps(ctx, tmp_path)
    deps.book.seen_urls.add(PAGE_URL)
    out = await read_page(_pai(deps), PAGE_URL, "super price")
    assert out.startswith('<evidence id="E1" untrusted="true"') and 'tier="1"' in out
    assert "KSh 190" in out
    # a url returned by memory_lookup is allowed without a prior search
    deps2 = _deps(ctx, tmp_path, memory=FakeMemory([("k1", _spec(PAGE_URL))]))
    assert (await read_page(_pai(deps2), PAGE_URL, "x")).startswith("error: unknown")
    memory_lookup(_pai(deps2), "x")
    assert (await read_page(_pai(deps2), PAGE_URL, "x")).startswith("<evidence")


async def test_list_links_filters(ctx, tmp_path, respx_mock, data_net):
    html = (
        '<a href="/files/a.pdf">Price list</a><a href="/about">About</a>'
        '<a href="https://x.org/b.pdf">B</a><a href="mailto:a@b.c">m</a>'
        '<a href="/files/a.pdf">dup</a>'
    )
    respx_mock.get(PAGE_URL).respond(200, content=html.encode())
    deps = _deps(ctx, tmp_path)
    deps.book.seen_urls.add(PAGE_URL)
    out = await list_links(_pai(deps), PAGE_URL, ".pdf")
    assert out.splitlines() == [
        "[1] Price list — https://www.epra.go.ke/files/a.pdf",
        "[2] B — https://x.org/b.pdf",
    ]
    assert "https://www.epra.go.ke/files/a.pdf" in deps.book.seen_urls
    assert await list_links(_pai(deps), PAGE_URL, "zzz") == "no links found"
    assert (await list_links(_pai(deps), "https://nope.com", "")).startswith("error: unknown url")


async def test_preview_table_xlsx(ctx, tmp_path, respx_mock, data_net):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Town", "Super"])
    for i in range(8):
        ws.append([f"T{i}", 180 + i])
    buf = io.BytesIO()
    wb.save(buf)
    url = "https://www.epra.go.ke/p.xlsx"
    respx_mock.get(url).respond(200, content=buf.getvalue())
    deps = _deps(ctx, tmp_path)
    deps.book.seen_urls.add(url)
    out = await preview_table(_pai(deps), url)
    lines = out.splitlines()
    assert lines[0].startswith("table 0 of 1") and lines[1] == "Town | Super"
    assert len(lines) == 2 + 5 and lines[2] == "T0 | 180"
    assert "out of range" in await preview_table(_pai(deps), url, table_index=3)


def test_registry_lookup_matches_enabled_only(ctx, tmp_path):
    catalog = load_catalog(ctx.home)
    on = catalog["wb:FP.CPI.TOTL.ZG"]
    off = CatalogEntry(**{**on.model_dump(), "key": "wb:off", "enabled": False})
    deps = _deps(ctx, tmp_path, catalog={on.key: on, off.key: off})
    out = registry_lookup(_pai(deps), "inflation kenya")
    assert (
        out
        == "wb:FP.CPI.TOTL.ZG — Kenya inflation, consumer prices (annual %) — World Bank — tier 1"
    )
    assert registry_lookup(_pai(deps), "zzzz") == "no registry series match"


async def test_tool_errors_are_strings(ctx, tmp_path, respx_mock, data_net):
    respx_mock.get(PAGE_URL).respond(404)
    deps = _deps(ctx, tmp_path, search=FakeSearch(exc=SearchError("all providers failed")))
    deps.book.seen_urls.add(PAGE_URL)
    assert (await web_search(_pai(deps), "q")).startswith("error: ")
    for out in (
        await read_page(_pai(deps), PAGE_URL, "x"),
        await list_links(_pai(deps), PAGE_URL),
        await preview_table(_pai(deps), PAGE_URL),
    ):
        assert out.startswith("error: ")
    assert all("error" in line for line in deps.log if "rejected" not in line)


async def test_search_budget_message(ctx, tmp_path):
    deps = _deps(ctx, tmp_path, search=FakeSearch(exc=BudgetExceeded("no credits")))
    assert await web_search(_pai(deps), "q") == "budget exhausted: stop searching"


def test_memory_lookup(ctx, tmp_path):
    deps = _deps(ctx, tmp_path, memory=FakeMemory([("k1", _spec())]))
    assert memory_lookup(_pai(deps), "x") == "k1 — Pub — https://a.go.ke/p"
    assert "https://a.go.ke/p" in deps.memory_urls
    assert memory_lookup(_pai(_deps(ctx, tmp_path)), "x") == "no remembered sources"


def test_secrets_redacted_in_log(ctx, tmp_path):
    deps = _deps(ctx, tmp_path)
    memory_lookup(_pai(deps), "fake-deepseek")
    assert "fake-deepseek" not in " ".join(deps.log)


@pytest.mark.parametrize("name", ["web_search", "read_page", "list_links", "preview_table"])
def test_tools_are_registrable(name):
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    import kenya_data_engine.research.tools as t

    Agent(TestModel(), deps_type=ResearchDeps, tools=[getattr(t, name)])


def test_registry_urls_cached(ctx, tmp_path):
    deps = _deps(ctx, tmp_path, catalog=load_catalog(ctx.home))
    first = tools._registry_urls(deps)
    assert tools._registry_urls(deps) is first


async def test_tools_never_raise(ctx, tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(tools, "policy_fetch", boom)
    deps = _deps(ctx, tmp_path, search=FakeSearch(exc=RuntimeError("kaboom")))
    deps.book.seen_urls.add(PAGE_URL)

    def sync_boom(*a, **k):
        raise RuntimeError("kaboom")

    deps.memory = SimpleNamespace(search=sync_boom)

    async def ok(*a, **k):
        return True

    monkeypatch.setattr(tools, "robots_allowed", ok)
    for out in (
        await web_search(_pai(deps), "q"),
        await list_links(_pai(deps), PAGE_URL),
        await preview_table(_pai(deps), PAGE_URL),
    ):
        assert out == "error: kaboom"
    assert memory_lookup(_pai(deps), "q") == "error: kaboom"


async def test_robots_disallowed(ctx, tmp_path, respx_mock, data_net):
    respx_mock.get("https://www.epra.go.ke/robots.txt").respond(
        200, content=b"User-agent: *\nDisallow: /prices\n"
    )
    deps = _deps(ctx, tmp_path)
    deps.book.seen_urls.add(PAGE_URL)
    for out in (
        await read_page(_pai(deps), PAGE_URL, "x"),
        await list_links(_pai(deps), PAGE_URL),
        await preview_table(_pai(deps), PAGE_URL),
    ):
        assert out == "error: disallowed by robots.txt"


async def test_final_url_and_redaction_in_seen(ctx, tmp_path, respx_mock, data_net):
    respx_mock.get("https://old.epra.go.ke/a").respond(
        302, headers={"location": "https://www.epra.go.ke/new"}
    )
    respx_mock.get("https://www.epra.go.ke/new").respond(200, content=b'<a href="/z">z</a>')
    deps = _deps(ctx, tmp_path)
    deps.book.seen_urls.add("https://old.epra.go.ke/a")
    await list_links(_pai(deps), "https://old.epra.go.ke/a")
    assert "https://www.epra.go.ke/new" in deps.book.seen_urls
    search = FakeSearch(
        [SearchResult(title="t", url="https://x.com/?k=fake-deepseek", snippet="s")]
    )
    deps2 = _deps(ctx, tmp_path, search)
    await web_search(_pai(deps2), "q")
    assert not any("fake-deepseek" in u for u in deps2.book.seen_urls)


async def test_secret_url_shown_redacted_and_redacted_form_accepted(ctx, tmp_path):
    raw = "https://x.com/?k=fake-deepseek"
    search = FakeSearch([SearchResult(title="t", url=raw, snippet="s fake-deepseek")])
    deps = _deps(ctx, tmp_path, search)
    out = await web_search(_pai(deps), "q")
    assert "fake-deepseek" not in out
    shown = out.split(" — ")[1]
    assert tools.url_allowed(deps, shown) and tools.url_allowed(deps, raw)
    deps.memory = FakeMemory([("k", _spec("https://m.com/?t=fake-deepseek"))])
    assert "fake-deepseek" not in memory_lookup(_pai(deps), "q")
    assert tools.url_allowed(deps, "https://m.com/?t=" + ctx.tracer.redact("fake-deepseek"))


def test_memory_lookup_without_url(ctx, tmp_path):
    deps = _deps(ctx, tmp_path, memory=FakeMemory([("k", _spec(None))]))
    assert memory_lookup(_pai(deps), "q") == "k — Pub — (no url)"


async def test_list_links_survives_malformed_href(ctx, tmp_path, respx_mock, data_net):
    html = '<a href="http://[::1">bad</a><a href="/ok">ok</a>'
    respx_mock.get(PAGE_URL).respond(200, content=html.encode())
    deps = _deps(ctx, tmp_path)
    deps.book.seen_urls.add(PAGE_URL)
    out = await list_links(_pai(deps), PAGE_URL)
    assert out == "[1] ok — https://www.epra.go.ke/ok"


async def test_post_fetch_formatting_errors_are_strings(ctx, tmp_path, monkeypatch):
    deps = _deps(ctx, tmp_path, SimpleNamespace(search=None))
    out = await web_search(_pai(deps), "q")  # search attribute is not callable
    assert out.startswith("error: ")


async def test_redirect_into_disallowed_path(ctx, tmp_path, respx_mock, data_net):
    respx_mock.get("https://www.epra.go.ke/robots.txt").respond(
        200, content=b"User-agent: *\nDisallow: /private\n"
    )
    respx_mock.get("https://www.epra.go.ke/ok").respond(
        302, headers={"location": "https://www.epra.go.ke/private/x"}
    )
    respx_mock.get("https://www.epra.go.ke/private/x").respond(200, content=b"<p>secret</p>" * 50)
    deps = _deps(ctx, tmp_path)
    deps.book.seen_urls.add("https://www.epra.go.ke/ok")
    for fn in (read_page, list_links, preview_table):
        args = ("x",) if fn is read_page else ()
        assert await fn(_pai(deps), "https://www.epra.go.ke/ok", *args) == (
            "error: disallowed by robots.txt"
        )
    assert deps.book.items == []
