import pytest

from kenya_data_engine.errors import FetchError
from kenya_data_engine.tools.fetch import fetch_page

URL = "https://example.co.ke/news/story"
PARA = (
    "The Central Bank of Kenya held its benchmark rate steady on Tuesday, citing "
    "easing inflation and a stable shilling across the first three quarters."
)
ARTICLE = f"""<html><head><title>Rate held</title></head><body>
<nav>Home | About</nav>
<article><h1>Rate held</h1><p>{PARA}</p>
<p>Analysts had expected a cut, but the monetary policy committee chose caution given
global uncertainty, food prices and the pace of government borrowing this year.</p></article>
<footer>copyright</footer></body></html>"""
SHELL = "<html><head><title>Shell</title></head><body><div id='app'></div></body></html>"
JINA_TEXT = "Title: Rate held\n\n" + PARA * 3


async def test_fetch_page_extracts_article(respx_mock, ctx):
    respx_mock.get(URL).respond(text=ARTICLE, headers={"content-type": "text/html"})
    page = await fetch_page(URL, ctx)
    assert page.via == "direct" and PARA in page.text
    assert page.title == "Rate held" and page.url == URL
    assert page.fetched_at.tzinfo is not None


async def test_fetch_page_falls_back_to_jina(respx_mock, ctx):
    respx_mock.get(URL).respond(text=SHELL)
    jina = respx_mock.get(f"https://r.jina.ai/{URL}").respond(text=JINA_TEXT)
    page = await fetch_page(URL, ctx)
    assert page.via == "jina" and PARA in page.text
    assert jina.calls.last.request.headers["Authorization"] == "Bearer fake-jina"


async def test_jina_without_key_sends_no_auth(respx_mock, ctx):
    ctx.secrets.jina_api_key = None
    respx_mock.get(URL).respond(text=SHELL)
    jina = respx_mock.get(f"https://r.jina.ai/{URL}").respond(text=JINA_TEXT)
    await fetch_page(URL, ctx)
    assert "Authorization" not in jina.calls.last.request.headers


async def test_fetch_page_raises_when_both_paths_thin(respx_mock, ctx):
    respx_mock.get(URL).respond(text=SHELL)
    respx_mock.get(f"https://r.jina.ai/{URL}").respond(text="tiny")
    with pytest.raises(FetchError):
        await fetch_page(URL, ctx)


async def test_fetch_page_raises_when_jina_errors(respx_mock, ctx):
    respx_mock.get(URL).respond(text=SHELL)
    respx_mock.get(f"https://r.jina.ai/{URL}").respond(404)
    with pytest.raises(FetchError):
        await fetch_page(URL, ctx)
