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


async def test_http_tally_counts_calls_and_cache_hits(respx_mock, cache):
    from kenya_data_engine.http import HTTP_TALLY

    respx_mock.get("https://a.ke/t").respond(200, text="hi")
    tally = {"http": 0, "cache_hits": 0}
    token = HTTP_TALLY.set(tally)
    try:
        async with httpx.AsyncClient() as client:
            await fetch("https://a.ke/t", client=client, cache=cache, ttl_hours=1)
            await fetch("https://a.ke/t", client=client, cache=cache, ttl_hours=1)
    finally:
        HTTP_TALLY.reset(token)
    assert tally == {"http": 2, "cache_hits": 1}


# ---- policy fetches -------------------------------------------------------------------------
import gzip  # noqa: E402

from kenya_data_engine.http import FetchPolicy  # noqa: E402
from kenya_data_engine.tools.urlpolicy import UnsafeUrl  # noqa: E402


def _policy(max_bytes: int = 1000, **kw) -> FetchPolicy:
    async def resolve(host: str) -> list[str]:
        return ["41.89.10.10"]

    return FetchPolicy(max_bytes=max_bytes, resolve=resolve, **kw)


async def test_policy_gzip_body_decodes(respx_mock, cache):
    body = b"hello gzip " * 20
    respx_mock.get("https://a.ke/z").respond(
        200, content=gzip.compress(body), headers={"content-encoding": "gzip"}
    )
    async with httpx.AsyncClient() as c:
        r = await fetch("https://a.ke/z", client=c, cache=cache, ttl_hours=1, policy=_policy())
    assert r.content == body


async def test_policy_cache_hit_is_checked(cache):
    cache.put("https://a.ke/x", b"x" * 50, "text/plain", 1)
    async with httpx.AsyncClient() as c:
        r = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy())
        assert r.from_cache
        with pytest.raises(FetchError, match="too large"):
            await fetch(
                "https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy(max_bytes=10)
            )
    cache.put("http://127.0.0.1/x", b"secret", "text/plain", 1)
    async with httpx.AsyncClient() as c:
        with pytest.raises(UnsafeUrl):
            await fetch("http://127.0.0.1/x", client=c, cache=cache, ttl_hours=1, policy=_policy())


async def test_cross_origin_redirect_drops_credentials(respx_mock, cache):
    respx_mock.get("https://a.ke/x").respond(302, headers={"location": "https://b.ke/y"})
    respx_mock.get("https://b.ke/y").respond(200, text="ok")
    hdrs = {"Authorization": "Bearer t", "Cookie": "s=1", "X-Other": "keep"}
    async with httpx.AsyncClient() as c:
        await fetch(
            "https://a.ke/x", client=c, cache=cache, ttl_hours=1, headers=hdrs, policy=_policy()
        )
    first, second = (call.request.headers for call in respx_mock.calls)
    assert first["authorization"] == "Bearer t" and first["cookie"] == "s=1"
    assert "authorization" not in second and "cookie" not in second
    assert second["x-other"] == "keep"


async def test_same_origin_redirect_keeps_credentials(respx_mock, cache):
    respx_mock.get("https://a.ke/x").respond(302, headers={"location": "/y"})
    respx_mock.get("https://a.ke/y").respond(200, text="ok")
    async with httpx.AsyncClient() as c:
        await fetch(
            "https://a.ke/x",
            client=c,
            cache=cache,
            ttl_hours=1,
            headers={"Authorization": "Bearer t"},
            policy=_policy(),
        )
    assert respx_mock.calls[1].request.headers["authorization"] == "Bearer t"


async def test_policy_chain_error_uses_aia_client(respx_mock, cache):
    chain_err = httpx.ConnectError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer"
    )
    respx_mock.get("https://a.ke/x").mock(side_effect=[chain_err, httpx.Response(200, text="ok")])
    used = []

    class FakeAia:
        async def client_for(self, url: str) -> httpx.AsyncClient:
            used.append(url)
            return httpx.AsyncClient()

    async with httpx.AsyncClient() as c:
        r = await fetch(
            "https://a.ke/x",
            client=c,
            cache=cache,
            ttl_hours=1,
            aia=FakeAia(),  # type: ignore[arg-type]
            policy=_policy(),
        )
    assert r.content == b"ok" and used == ["https://a.ke/x"]
