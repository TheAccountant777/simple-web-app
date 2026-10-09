from datetime import UTC, datetime
from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser
from sourcegen import WORDS, listing_html

from kenya_data_engine.config import SourceSpec, load_sources
from kenya_data_engine.errors import FetchError
from kenya_data_engine.radar.base import build_adapters
from kenya_data_engine.radar.listing import ListingAdapter

FIX = Path(__file__).parent / "fixtures" / "listing"
OLD = datetime(2000, 1, 1, tzinfo=UTC)
URL = "https://pages.example.ke/list"


def spec(**kw):
    base = dict(
        type="listing",
        url=URL,
        kind="policy",
        item="article",
        title="h2 a",
        link="h2 a",
        date="time",
    )
    return SourceSpec(**{**base, **kw})


def adapter(sp=None, max_items=10):
    return ListingAdapter("x", sp or spec(), max_items)


def _packaged_listings():
    from kenya_data_engine.home import EngineHome

    src = load_sources(EngineHome(Path("/nonexistent")))
    return sorted(n for n, s in src.specs.items() if s.type == "listing")


@pytest.mark.parametrize("name", _packaged_listings())
async def test_every_packaged_listing_parses_from_its_own_selectors(name, respx_mock, ctx):
    sp = load_sources(ctx.home).specs[name]
    respx_mock.get(sp.url).respond(200, content=listing_html(name, sp).encode())
    sigs = await ListingAdapter(name, sp, ctx.config.radar.max_items).fetch(ctx, since=OLD)
    assert len(sigs) == 3
    assert [s.title for s in sigs] == [f"{name} headline {w}" for w in WORDS]
    assert all(s.url and s.url.startswith("http") for s in sigs)
    assert all(s.kind == sp.kind and s.source == name for s in sigs)
    assert all((s.published_at is not None) == bool(sp.date) for s in sigs)


async def test_parliament_real_page_fixture_parses(respx_mock, ctx):
    sp = load_sources(ctx.home).specs["parliament"]
    respx_mock.get(sp.url).respond(200, content=(FIX / "parliament.html").read_bytes())
    sigs = await ListingAdapter("parliament", sp, 10).fetch(ctx, since=OLD)
    assert 1 <= len(sigs) <= 10
    assert all(s.title.strip() and s.url and s.url.startswith("http") for s in sigs)
    assert all(s.published_at is not None for s in sigs)


def test_selectolax_supports_comma_unions_in_css_first():
    tree = LexborHTMLParser('<div><h3>Union</h3><span class="date">1 Jan 2026</span></div>')
    assert tree.css_first("h2, h3, .article-title").text() == "Union"
    assert tree.css_first("time, span.date").text() == "1 Jan 2026"
    assert len(tree.css("table tbody tr, div")) == 1


async def test_layout_change_raises_fetch_error(respx_mock, ctx):
    respx_mock.get(URL).respond(200, text="<html><body>new layout</body></html>")
    with pytest.raises(FetchError, match="selector matched nothing") as ei:
        await adapter().fetch(ctx, OLD)
    assert ei.value.hint == "run `engine sources test x`"


async def test_relative_links_made_absolute(respx_mock, ctx):
    respx_mock.get(URL).respond(
        200, text='<article><h2><a href="/2026/09/15/bulletin/">Bulletin</a></h2></article>'
    )
    sigs = await adapter().fetch(ctx, OLD)
    assert [s.url for s in sigs] == ["https://pages.example.ke/2026/09/15/bulletin/"]


async def test_since_drops_old_keeps_undated_and_max_items(respx_mock, ctx):
    rows = "".join(
        f'<article><h2><a href="/p{i}">Post {i}</a></h2><time>{d}</time></article>'
        for i, d in enumerate(["October 7, 2026", "garbage date", "January 1, 2020"])
    )
    rows += '<article><h2><a href="/nodate">No date</a></h2></article>'
    respx_mock.get(URL).respond(200, text=f"<html><body>{rows}</body></html>")
    since = datetime(2026, 1, 1, tzinfo=UTC)
    sigs = await adapter().fetch(ctx, since)
    assert [s.title for s in sigs] == ["Post 0", "Post 1", "No date"]
    assert sigs[1].published_at is None
    capped = await adapter(max_items=2).fetch(ctx, since)
    assert [s.title for s in capped] == ["Post 0", "Post 1"]


async def test_item_without_title_or_href_skipped_not_an_error(respx_mock, ctx):
    html = (
        "<article><span>x</span></article>"  # no title
        "<article><h2><a>No href</a></h2></article>"  # no href
        '<article><h2><a href="/a">Real</a></h2></article>'
    )
    respx_mock.get(URL).respond(200, text=html)
    assert [s.title for s in await adapter().fetch(ctx, OLD)] == ["Real"]


async def test_link_takes_first_anchor_with_an_href(respx_mock, ctx):
    html = (
        '<div class="i"><h3>Title</h3><a name="top">x</a>'
        '<a href="/first">1</a><a href="/second">2</a></div>'
    )
    respx_mock.get(URL).respond(200, text=html)
    sp = spec(item="div.i", title="h3", link="a", date=None)
    sigs = await adapter(sp).fetch(ctx, OLD)
    assert [s.url for s in sigs] == ["https://pages.example.ke/first"]


def test_build_adapters_includes_enabled_listings_only(ctx, tmp_home):
    names = {a.name for a in build_adapters(ctx.config, ctx.home)}
    assert {"parliament", "cbk_news", "knbs_recent_releases", "trends24_kenya"} <= names
    tmp_home.sources_path.write_text("cbk_news:\n  enabled: false\n")
    assert "cbk_news" not in {a.name for a in build_adapters(ctx.config, ctx.home)}


async def test_union_item_selector_does_not_duplicate_items(respx_mock, ctx):
    html = '<table class="v"><tbody><tr><td><a href="/a">Only</a></td></tr></tbody></table>'
    respx_mock.get(URL).respond(200, text=html)
    sp = spec(item="table.v tbody tr, table tbody tr", title="td a", link="td a", date=None)
    assert [s.title for s in await adapter(sp).fetch(ctx, OLD)] == ["Only"]
