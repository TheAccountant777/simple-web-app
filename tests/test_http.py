from datetime import UTC, datetime

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
    cache.put("policy:https://a.ke/x", b"x" * 50, "text/plain", 1, "https://a.ke/x")
    async with httpx.AsyncClient() as c:
        r = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy())
        assert r.from_cache
        with pytest.raises(FetchError, match="too large"):
            await fetch(
                "https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy(max_bytes=10)
            )
    cache.put("policy:http://127.0.0.1/x", b"secret", "text/plain", 1, "http://127.0.0.1/x")
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


async def test_legacy_cache_entry_is_not_served_to_a_policy_fetch(respx_mock, cache):
    cache.put("https://a.ke/x", b"unchecked legacy bytes", "text/plain", 1)  # no policy applied
    route = respx_mock.get("https://a.ke/x").respond(200, text="fresh")
    async with httpx.AsyncClient() as c:
        r = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy())
        assert not r.from_cache and r.content == b"fresh" and route.call_count == 1
        plain = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
    assert plain.content == b"unchecked legacy bytes"  # the plain namespace is untouched


async def test_policy_cache_hit_returns_final_url_and_fetch_time(respx_mock, cache):
    respx_mock.get("https://a.ke/x").respond(302, headers={"location": "https://a.ke/final"})
    respx_mock.get("https://a.ke/final").respond(200, text="ok")
    async with httpx.AsyncClient() as c:
        first = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy())
        again = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy())
    assert first.url == again.url == "https://a.ke/final"
    assert again.from_cache and again.fetched_at <= datetime.now(UTC)
    assert cache.get("policy:https://a.ke/x").final_url == "https://a.ke/final"


async def test_cache_key_includes_accept_header(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").respond(200, text="hi")
    async with httpx.AsyncClient() as c:
        await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, headers={"Accept": "a/b"})
        again = await fetch(
            "https://a.ke/x", client=c, cache=cache, ttl_hours=1, headers={"accept": "a/b"}
        )
        other = await fetch(
            "https://a.ke/x", client=c, cache=cache, ttl_hours=1, headers={"Accept": "c/d"}
        )
    assert again.from_cache and not other.from_cache and route.call_count == 2


def test_cache_migrates_an_old_schema_idempotently(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE cache (key TEXT PRIMARY KEY, content BLOB, content_type TEXT, "
            "fetched_at TEXT, expires_at TEXT)"
        )
    Cache(db)
    c = Cache(db)  # second open must not fail on the existing column
    c.put("k", b"v", "t", 1, "https://f/")
    entry = c.get("k")
    assert entry is not None and entry.final_url == "https://f/"


class _FakeStream:
    def __init__(self, addr):
        self.addr = addr

    def get_extra_info(self, name):
        return self.addr if name == "server_addr" else None


async def test_connected_peer_must_be_global(respx_mock, cache):
    def reply(addr):
        return httpx.Response(200, text="x", extensions={"network_stream": _FakeStream(addr)})

    respx_mock.get("https://rebind.ke/x").mock(return_value=reply(("169.254.169.254", 443)))
    respx_mock.get("https://ok.ke/x").mock(return_value=reply(("93.184.216.34", 443)))
    respx_mock.get("https://mock.ke/x").respond(200, text="no peer info: pre-connect check only")
    async with httpx.AsyncClient(trust_env=False) as c:
        with pytest.raises(UnsafeUrl, match="non-public"):
            await fetch("https://rebind.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy())
        ok = await fetch("https://ok.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy())
        assert ok.content == b"x"
        await fetch("https://mock.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy())
    assert cache.get("policy:https://rebind.ke/x") is None


async def _rebind_raises(client, cache):
    with pytest.raises(UnsafeUrl, match="non-public"):
        await fetch(
            "https://rebind.ke/x", client=client, cache=cache, ttl_hours=1, policy=_policy()
        )


def _rebind_reply(respx_mock):
    resp = httpx.Response(
        200, text="x", extensions={"network_stream": _FakeStream(("169.254.169.254", 443))}
    )
    respx_mock.get("https://rebind.ke/x").mock(return_value=resp)


async def test_peer_check_applies_with_no_proxy_env(respx_mock, cache, monkeypatch):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "rebind.ke")
    for name in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, "http://proxy.invalid:3128")
    _rebind_reply(respx_mock)
    async with httpx.AsyncClient(trust_env=True) as c:
        assert c._mounts  # a None mount for NO_PROXY exists, plus a real proxy mount
        assert c._transport_for_url(httpx.URL("https://rebind.ke/x")) is c._transport
        await _rebind_raises(c, cache)


async def test_peer_check_applies_with_none_mount(respx_mock, cache):
    _rebind_reply(respx_mock)
    async with httpx.AsyncClient(trust_env=False, mounts={"all://other.example": None}) as c:
        await _rebind_raises(c, cache)


async def test_peer_check_skipped_for_proxied_transport(respx_mock, cache):
    _rebind_reply(respx_mock)
    proxy = httpx.AsyncHTTPTransport()
    async with httpx.AsyncClient(trust_env=False, mounts={"all://rebind.ke": proxy}) as c:
        got = await fetch(
            "https://rebind.ke/x", client=c, cache=cache, ttl_hours=1, policy=_policy()
        )
        assert got.content == b"x"


def test_peer_check_fails_safe_without_httpx_internals():
    from kenya_data_engine.http import _check_peer

    resp = httpx.Response(200, extensions={"network_stream": _FakeStream(("169.254.169.254", 443))})
    with pytest.raises(UnsafeUrl, match="non-public"):
        _check_peer(resp, object(), "https://rebind.ke/x")  # type: ignore[arg-type]
