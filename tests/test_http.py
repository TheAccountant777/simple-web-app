import httpx
import pytest
import tenacity

from kenya_data_engine import __version__
from kenya_data_engine.cache import Cache
from kenya_data_engine.errors import FetchError
from kenya_data_engine.http import fetch


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "engine.db")


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("kenya_data_engine.http._WAIT", tenacity.wait_none())


async def test_second_fetch_hits_cache(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").respond(200, text="hi")
    async with httpx.AsyncClient() as c:
        first = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
        r = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
    assert not first.from_cache and first.content == b"hi"
    assert r.from_cache and r.content == b"hi" and route.call_count == 1


async def test_retries_5xx_then_succeeds(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, text="ok")]
    )
    async with httpx.AsyncClient() as c:
        r = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
    assert r.status == 200 and route.call_count == 3


async def test_retries_transport_error(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").mock(
        side_effect=[httpx.ConnectError("down"), httpx.Response(200, text="ok")]
    )
    async with httpx.AsyncClient() as c:
        r = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
    assert r.status == 200 and route.call_count == 2


async def test_exhausted_retries_raise_and_do_not_cache(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").respond(500)
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError, match=r"500 for https://a\.ke/x"):
            await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
    assert route.call_count == 3 and cache.get("https://a.ke/x") is None


async def test_exhausted_transport_errors_raise_fetch_error(respx_mock, cache):
    respx_mock.get("https://a.ke/x").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError):
            await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)


async def test_404_raises_fetch_error_without_retry(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").respond(404)
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError, match="404 for"):
            await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
    assert route.call_count == 1


async def test_sends_user_agent_and_extra_headers(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").respond(200, text="hi")
    async with httpx.AsyncClient() as c:
        await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, headers={"X-A": "1"})
    req = route.calls.last.request
    ua = req.headers["user-agent"]
    assert ua.startswith("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
    assert "Chrome/124.0 Safari/537.36" in ua and ua.endswith(f"kenya-data-engine/{__version__}")
    assert (
        req.headers["accept"] == "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    )
    assert req.headers["x-a"] == "1"


async def test_header_override_replaces_the_default_user_agent(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").respond(200, text="hi")
    async with httpx.AsyncClient() as c:
        await fetch(
            "https://a.ke/x", client=c, cache=cache, ttl_hours=1, headers={"User-Agent": "mine/1"}
        )
    assert route.calls.last.request.headers["user-agent"] == "mine/1"


async def test_cache_key_is_normalized_url(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").respond(200, text="hi")
    async with httpx.AsyncClient() as c:
        await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
        r = await fetch("https://a.ke/x#frag", client=c, cache=cache, ttl_hours=1)
    assert r.from_cache and route.call_count == 1
