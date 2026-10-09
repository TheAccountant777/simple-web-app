from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenya_data_engine.errors import FetchError
from kenya_data_engine.radar.base import build_adapters
from kenya_data_engine.radar.listing import ListingAdapter

FIX = Path(__file__).parent / "fixtures" / "listing"
OLD = datetime(2000, 1, 1, tzinfo=UTC)


def adapter_for(name, ctx):
    return ListingAdapter(name, ctx.config.radar.listings[name], ctx.config.radar.max_items)


@pytest.mark.parametrize("name", ["cbk", "knbs", "epra", "parliament"])
async def test_listing_fixture_parses(name, respx_mock, ctx):
    spec = ctx.config.radar.listings[name]
    respx_mock.get(spec.url).respond(200, content=(FIX / f"{name}.html").read_bytes())
    sigs = await adapter_for(name, ctx).fetch(ctx, since=OLD)
    assert 1 <= len(sigs) <= ctx.config.radar.max_items
    assert all(s.title.strip() and s.url and s.url.startswith("http") for s in sigs)
    assert all(s.kind == spec.kind and s.source == name for s in sigs)
    assert all(s.published_at is not None for s in sigs)


async def test_layout_change_raises_fetch_error(respx_mock, ctx):
    spec = ctx.config.radar.listings["cbk"]
    respx_mock.get(spec.url).respond(200, text="<html><body>new layout</body></html>")
    with pytest.raises(FetchError, match="selector matched nothing") as ei:
        await adapter_for("cbk", ctx).fetch(ctx, OLD)
    assert ei.value.hint == "run engine doctor"


async def test_relative_links_made_absolute(respx_mock, ctx):
    spec = ctx.config.radar.listings["cbk"]
    respx_mock.get(spec.url).respond(200, content=(FIX / "cbk.html").read_bytes())
    sigs = await adapter_for("cbk", ctx).fetch(ctx, OLD)
    assert "https://www.centralbank.go.ke/2026/09/15/weekly-bulletin/" in {s.url for s in sigs}


async def test_since_drops_old_keeps_undated_and_max_items(respx_mock, ctx):
    spec = ctx.config.radar.listings["cbk"]
    rows = "".join(
        f'<article><h2><a href="/p{i}">Post {i}</a></h2><time>{d}</time></article>'
        for i, d in enumerate(["October 7, 2026", "garbage date", "January 1, 2020"])
    )
    rows += '<article><h2><a href="/nodate">No date</a></h2></article>'
    respx_mock.get(spec.url).respond(200, text=f"<html><body>{rows}</body></html>")
    since = datetime(2026, 1, 1, tzinfo=UTC)
    sigs = await adapter_for("cbk", ctx).fetch(ctx, since)
    assert [s.title for s in sigs] == ["Post 0", "Post 1", "No date"]
    assert sigs[1].published_at is None
    capped = await ListingAdapter("cbk", spec, 2).fetch(ctx, since)
    assert [s.title for s in capped] == ["Post 0", "Post 1"]


async def test_item_without_title_skipped(respx_mock, ctx):
    spec = ctx.config.radar.listings["cbk"]
    html = '<article><span>x</span></article><article><h2><a href="/a">Real</a></h2></article>'
    respx_mock.get(spec.url).respond(200, text=html)
    assert [s.title for s in await adapter_for("cbk", ctx).fetch(ctx, OLD)] == ["Real"]


def test_build_adapters_includes_enabled_listings(ctx):
    names = {a.name for a in build_adapters(ctx.config, ctx.home)}
    assert {"cbk", "knbs", "epra", "parliament"} <= names
    cfg = ctx.config.model_copy(deep=True)
    cfg.radar.enabled = ["epra"]
    assert [a.name for a in build_adapters(cfg, ctx.home)] == ["epra"]
