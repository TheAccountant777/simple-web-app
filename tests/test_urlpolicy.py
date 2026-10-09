import io
import zipfile

import httpx
import pytest
import tenacity

from kenya_data_engine.cache import Cache
from kenya_data_engine.errors import FetchError
from kenya_data_engine.http import FetchPolicy, fetch
from kenya_data_engine.tools.urlpolicy import UnsafeUrl, check_url, sniff


def resolver(*ips: str):
    async def resolve(host: str) -> list[str]:
        return list(ips)

    return resolve


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "engine.db")


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("kenya_data_engine.http._WAIT", tenacity.wait_none())


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://a.ke/x", "data:text/plain,hi"])
async def test_rejects_non_http_schemes(url):
    with pytest.raises(UnsafeUrl):
        await check_url(url, resolver("41.89.10.10"))


async def test_rejects_userinfo_and_no_host():
    with pytest.raises(UnsafeUrl):
        await check_url("https://user:pw@a.ke/x", resolver("41.89.10.10"))
    with pytest.raises(UnsafeUrl):
        await check_url("https:///x", resolver("41.89.10.10"))


_PRIVATE = ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "::ffff:10.0.0.1"]


@pytest.mark.parametrize("ip", _PRIVATE)
async def test_rejects_private_resolution(ip):
    with pytest.raises(UnsafeUrl):
        await check_url("https://a.ke/x", resolver(ip))


async def test_rejects_if_any_ip_private_or_unresolvable():
    with pytest.raises(UnsafeUrl):
        await check_url("https://a.ke/x", resolver("41.89.10.10", "10.0.0.5"))
    with pytest.raises(UnsafeUrl):
        await check_url("https://a.ke/x", resolver())
    with pytest.raises(UnsafeUrl):
        await check_url("https://a.ke/x", resolver("not-an-ip"))

    async def boom(host: str) -> list[str]:
        raise OSError("nxdomain")

    with pytest.raises(UnsafeUrl):
        await check_url("https://a.ke/x", boom)


async def test_accepts_public():
    await check_url("https://a.ke/x", resolver("41.89.10.10"))


async def test_default_resolver_literal_ip():
    with pytest.raises(UnsafeUrl):
        await check_url("http://127.0.0.1/x")


def policy(**kw) -> FetchPolicy:
    return FetchPolicy(max_bytes=kw.pop("max_bytes", 1000), resolve=resolver("41.89.10.10"), **kw)


async def test_redirect_to_private_blocked(respx_mock, cache):
    respx_mock.get("https://a.ke/x").respond(302, headers={"location": "http://127.0.0.1/x"})
    async with httpx.AsyncClient() as c:
        with pytest.raises(UnsafeUrl):
            await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=policy())


async def test_redirect_hop_resolving_private_blocked(respx_mock, cache):
    respx_mock.get("https://a.ke/x").respond(302, headers={"location": "https://evil.ke/y"})

    async def resolve(host: str) -> list[str]:
        return ["10.0.0.1"] if host == "evil.ke" else ["41.89.10.10"]

    async with httpx.AsyncClient() as c:
        with pytest.raises(UnsafeUrl):
            await fetch(
                "https://a.ke/x",
                client=c,
                cache=cache,
                ttl_hours=1,
                policy=FetchPolicy(max_bytes=100, resolve=resolve),
            )


async def test_too_many_redirects(respx_mock, cache):
    respx_mock.get("https://a.ke/x").respond(302, headers={"location": "/x"})
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError, match="too many redirects"):
            await fetch(
                "https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=policy(max_redirects=2)
            )


async def test_size_cap_aborts(respx_mock, cache):
    respx_mock.get("https://a.ke/x").respond(200, content=b"x" * 5000)
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError, match="too large"):
            await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=policy())
    assert cache.get("https://a.ke/x") is None


async def test_size_cap_declared_length(respx_mock, cache):
    respx_mock.get("https://a.ke/x").respond(
        200, content=b"x" * 10, headers={"content-length": "999999"}
    )
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError, match="too large"):
            await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=policy())


async def test_final_url_after_redirect(respx_mock, cache):
    respx_mock.get("https://a.ke/x").respond(301, headers={"location": "/final?a=1"})
    respx_mock.get("https://a.ke/final?a=1").respond(200, text="ok")
    async with httpx.AsyncClient() as c:
        r = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=policy())
    assert r.url == "https://a.ke/final?a=1" and r.content == b"ok"


async def test_policy_5xx_retries_and_errors_use_fetch_error(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").respond(500)
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError, match="500"):
            await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1, policy=policy())
    assert route.call_count == 3
    respx_mock.get("https://a.ke/y").respond(404)
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError, match="404"):
            await fetch("https://a.ke/y", client=c, cache=cache, ttl_hours=1, policy=policy())


def _zip(name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, "x")
    return buf.getvalue()


def test_sniff_types():
    assert sniff(b"%PDF-1.7 ...") == "pdf"
    assert sniff(_zip("xl/workbook.xml")) == "xlsx"
    assert sniff(_zip("word/document.xml")) == "unknown"
    assert sniff(b"PK\x03\x04garbage") == "unknown"
    assert sniff(bytes.fromhex("D0CF11E0A1B11AE1")) == "xls"
    assert sniff(b"  \n<!doctype html><html>") == "html"
    assert sniff(b"\xef\xbb\xbf<html>") == "html"
    assert sniff(b'{"a": 1}') == "json"
    assert sniff(b"[1, 2]") == "json"
    assert sniff(b"a,b\n1,2\n") == "csv"
    assert sniff("KSh,é\n1,2".encode()) == "csv"
    assert sniff(b"\x89PNG\xff\xfe\x00\x00") == "unknown"
    assert sniff(b"") == "unknown"
