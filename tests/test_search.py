import json

import httpx
import pytest

from kenya_data_engine.errors import ConfigError, SearchError
from kenya_data_engine.tools.search import (
    FallbackSearch,
    SerperProvider,
    TavilyProvider,
    build_search,
)

TAVILY = "https://api.tavily.com/search"
SERPER = "https://google.serper.dev/search"


async def test_tavily_maps_results(respx_mock, ctx):
    route = respx_mock.post(TAVILY).respond(
        json={"results": [{"title": "T", "url": "https://a.ke/x", "content": "snip"}]}
    )
    out = await TavilyProvider("k", ctx.http).search("cbk rate", n=3, domains=["cbk.go.ke"])
    assert (out[0].title, out[0].url, out[0].snippet) == ("T", "https://a.ke/x", "snip")
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer k"
    assert json.loads(req.content) == {
        "query": "cbk rate",
        "max_results": 3,
        "search_depth": "basic",
        "include_domains": ["cbk.go.ke"],
    }


async def test_serper_adds_site_filter(respx_mock, ctx):
    route = respx_mock.post(SERPER).respond(
        json={"organic": [{"title": "T", "link": "https://b.ke", "snippet": "s"}]}
    )
    out = await SerperProvider("k", ctx.http).search("rate", domains=["cbk.go.ke"])
    assert out[0].url == "https://b.ke" and out[0].snippet == "s"
    req = route.calls.last.request
    assert req.headers["X-API-KEY"] == "k"
    body = json.loads(req.content)
    assert "(site:cbk.go.ke)" in body["q"] and body["num"] == 5


async def test_serper_multiple_domains(respx_mock, ctx):
    route = respx_mock.post(SERPER).respond(json={})
    assert await SerperProvider("k", ctx.http).search("q", domains=["a.ke", "b.ke"]) == []
    assert "(site:a.ke OR site:b.ke)" in json.loads(route.calls.last.request.content)["q"]


async def test_fallback_uses_second_on_first_error(respx_mock, ctx):
    respx_mock.post(TAVILY).respond(500)
    respx_mock.post(SERPER).respond(
        json={"organic": [{"title": "S", "link": "https://s.ke", "snippet": "x"}]}
    )
    fb = FallbackSearch([TavilyProvider("k", ctx.http), SerperProvider("k", ctx.http)], ctx.tracer)
    assert fb.name == "fallback"
    out = await fb.search("q")
    assert out[0].title == "S"
    events = [
        json.loads(line)
        for line in ctx.run.trace_path.read_text().splitlines()
        if '"kind":"tool"' in line.replace(" ", "")
    ]
    assert [e["status"] for e in events] == ["error", "ok"]


async def test_all_fail_raises_search_error(respx_mock, ctx):
    respx_mock.post(TAVILY).mock(side_effect=httpx.ConnectError("boom"))
    respx_mock.post(SERPER).respond(429)
    fb = FallbackSearch([TavilyProvider("k", ctx.http), SerperProvider("k", ctx.http)], ctx.tracer)
    with pytest.raises(SearchError) as ei:
        await fb.search("q")
    assert ei.value.message == "all search providers failed (tavily: boom; serper: 429)"
    assert isinstance(ei.value.__cause__, httpx.HTTPStatusError)
    assert ei.value.hint == "check keys with engine doctor"


def test_build_search_without_keys_raises_config_error(ctx_no_keys):
    with pytest.raises(ConfigError) as ei:
        build_search(ctx_no_keys)
    assert ei.value.hint == "run engine init"


def test_build_search_keeps_only_providers_with_keys(ctx):
    ctx.secrets.serper_api_key = None
    fb = build_search(ctx)
    assert [p.name for p in fb.providers] == ["tavily"]


async def test_search_error_reasons_are_redacted_and_cover_timeouts(respx_mock, ctx):
    respx_mock.post(TAVILY).respond(401)
    respx_mock.post(SERPER).mock(side_effect=httpx.ReadTimeout("slow fake-serper"))
    fb = FallbackSearch([TavilyProvider("k", ctx.http), SerperProvider("k", ctx.http)], ctx.tracer)
    with pytest.raises(SearchError) as ei:
        await fb.search("q")
    assert ei.value.message == "all search providers failed (tavily: 401; serper: timeout)"
    assert "fake-serper" not in ei.value.message


async def test_tavily_omits_include_domains_when_none(respx_mock, ctx):
    route = respx_mock.post(TAVILY).respond(json={"results": []})
    await TavilyProvider("k", ctx.http).search("q")
    assert json.loads(route.calls.last.request.content) == {
        "query": "q",
        "max_results": 5,
        "search_depth": "basic",
    }


def _ledger(ctx):
    from kenya_data_engine.research.budget import Ledger

    return Ledger.from_config(ctx.config.research)


async def test_search_charges_credits_and_records_usage(respx_mock, ctx):
    from kenya_data_engine.tools.search import SearchUsage

    route = respx_mock.post(TAVILY).respond(json={"results": []})
    ledger = _ledger(ctx)
    usage = SearchUsage(ctx.home.db_path)
    fb = FallbackSearch([TavilyProvider("k", ctx.http)], ctx.tracer, ledger=ledger, usage=usage)
    await fb.search("q", depth="advanced")
    assert json.loads(route.calls[0].request.content)["search_depth"] == "advanced"
    assert ledger.snapshot().groups["scouts"].credits_spent == 2
    assert usage.month_total("tavily") == 2
    assert usage.month_total("serper") == 0


async def test_search_refuses_without_credits(respx_mock, ctx):
    from kenya_data_engine.errors import BudgetExceeded

    route = respx_mock.post(TAVILY).respond(json={"results": []})
    ledger = _ledger(ctx)
    ledger.charge_credits("scouts", 17)
    fb = FallbackSearch([TavilyProvider("k", ctx.http)], ctx.tracer, ledger=ledger)
    with pytest.raises(BudgetExceeded):
        await fb.search("q")
    assert not route.called


async def test_search_monthly_limit_skips_provider(respx_mock, ctx):
    from kenya_data_engine.tools.search import SearchUsage

    t = respx_mock.post(TAVILY).respond(json={"results": []})
    respx_mock.post(SERPER).respond(json={"organic": []})
    usage = SearchUsage(ctx.home.db_path)
    usage.add("tavily", 5)
    fb = FallbackSearch(
        [TavilyProvider("k", ctx.http), SerperProvider("k", ctx.http)],
        ctx.tracer, usage=usage, monthly_limit=5,
    )  # fmt: skip
    assert await fb.search("q") == []
    assert not t.called and usage.month_total("serper") == 1


def test_build_search_with_ledger_wires_usage(ctx):
    fb = build_search(ctx, ledger=_ledger(ctx), group="claims")
    assert fb._group == "claims" and fb._usage is not None and fb._monthly_limit == 1000
