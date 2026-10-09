from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import tenacity

from kenya_data_engine.errors import FetchError
from kenya_data_engine.radar.base import run_radar
from kenya_data_engine.radar.rss import RssAdapter

FIX = Path(__file__).parent / "fixtures" / "rss"
URL = "https://feeds.example.ke/rss"
T0 = datetime(2000, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("kenya_data_engine.http._WAIT", tenacity.wait_none())


@pytest.mark.parametrize("name", ["nation"])
async def test_rss_parses_fixture(name, respx_mock, ctx):
    respx_mock.get(URL).respond(200, content=(FIX / f"{name}.xml").read_bytes())
    sigs = await RssAdapter(name, URL).fetch(ctx, T0)
    assert len(sigs) == 3
    assert all(s.published_at and s.published_at >= T0 and s.source == name for s in sigs)
    assert all(s.kind == "news" and s.url and s.title for s in sigs)


async def test_rss_filters_since_and_strips_html(respx_mock, ctx):
    respx_mock.get(URL).respond(200, content=(FIX / "nation.xml").read_bytes())
    since = datetime(2026, 10, 8, tzinfo=UTC)
    sigs = await RssAdapter("nation", URL).fetch(ctx, since)
    assert len(sigs) == 2 and all(s.published_at >= since for s in sigs)
    maize = next(s for s in sigs if "Maize" in s.title)
    assert "<" not in maize.snippet and "Sh3,600" in maize.snippet


async def test_rss_trends_kind_and_snippet_truncated(respx_mock, ctx):
    long = "x" * 500
    xml = (
        '<rss version="2.0"><channel><title>t</title><item><title>Hot</title>'
        "<link>https://a.ke/h</link><pubDate>Fri, 09 Oct 2026 07:20:00 +0300</pubDate>"
        f"<description>{long}</description></item>"
        "<item><title>No date</title><link>https://a.ke/nd</link></item></channel></rss>"
    )
    respx_mock.get(URL).respond(200, content=xml.encode())
    sigs = await RssAdapter("google_trends", URL, kind="attention").fetch(ctx, T0)
    assert len(sigs) == 1  # undated entry dropped
    assert sigs[0].kind == "attention" and len(sigs[0].snippet) == 300


async def test_rss_garbage_yields_zero_without_error(respx_mock, ctx):  # Review Focus 3
    respx_mock.get(URL).respond(200, content=b"\xff\xfe<html>not a feed")
    assert await RssAdapter("x", URL).fetch(ctx, T0) == []
    res = await run_radar([RssAdapter("x", URL)], ctx, T0)
    assert res.signals == [] and res.errors == []


async def test_rss_empty_feed(respx_mock, ctx):
    xml = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'
    respx_mock.get(URL).respond(200, content=xml)
    assert await RssAdapter("x", URL).fetch(ctx, T0) == []


async def test_timeout_adapter_reported(respx_mock, ctx):
    route = respx_mock.get(URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(FetchError):
        await RssAdapter("x", URL).fetch(ctx, T0)
    assert route.call_count == 3
    res = await run_radar([RssAdapter("x", URL)], ctx, T0)
    assert res.signals == [] and [e.adapter for e in res.errors] == ["x"]


async def test_rss_uses_per_source_user_agent(respx_mock, ctx):
    route = respx_mock.get(URL).respond(200, content=b"<rss version='2.0'><channel/></rss>")
    await RssAdapter("x", URL, user_agent="python:custom/1").fetch(ctx, T0)
    assert route.calls.last.request.headers["user-agent"] == "python:custom/1"
