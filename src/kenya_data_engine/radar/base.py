"""Radar core: adapter protocol, dedupe, concurrent runner, stage."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

from kenya_data_engine.config import EngineConfig
from kenya_data_engine.context import RunContext
from kenya_data_engine.home import EngineHome
from kenya_data_engine.models import AdapterError, RadarResult, Signal, normalize_url
from kenya_data_engine.radar.calendar import CalendarAdapter
from kenya_data_engine.radar.listing import ListingAdapter
from kenya_data_engine.radar.rss import RssAdapter


class Adapter(Protocol):
    name: str

    async def fetch(self, ctx: RunContext, since: datetime) -> list[Signal]: ...


def _title_key(title: str) -> str:
    return " ".join(title.casefold().split())


def dedupe(signals: list[Signal]) -> list[Signal]:
    """Keep the first of each URL; also drop repeated normalized titles."""
    seen_keys: set[str] = set()
    seen_titles: set[str] = set()
    out: list[Signal] = []
    for s in signals:
        key = "url:" + normalize_url(s.url) if s.url else "title:" + _title_key(s.title)
        title = _title_key(s.title)
        if key in seen_keys or title in seen_titles:
            continue
        seen_keys.add(key)
        seen_titles.add(title)
        out.append(s)
    return out


async def run_radar(adapters: list[Adapter], ctx: RunContext, since: datetime) -> RadarResult:
    async def one(adapter: Adapter) -> list[Signal] | AdapterError:
        try:
            async with ctx.tracer.span("radar", "tool", adapter.name):
                return await adapter.fetch(ctx, since)
        except Exception as exc:
            return AdapterError(
                adapter=adapter.name, message=ctx.tracer.redact(str(exc) or type(exc).__name__)
            )

    results = await asyncio.gather(*(one(a) for a in adapters))
    signals: list[Signal] = []
    errors: list[AdapterError] = []
    for r in results:
        if isinstance(r, AdapterError):
            errors.append(r)
        else:
            signals.extend(r)
    unique = dedupe(signals)
    unique.sort(
        key=lambda s: (
            s.published_at is None,
            -(s.published_at.timestamp() if s.published_at else 0),
        )
    )
    return RadarResult(signals=unique, errors=errors, collected_at=datetime.now(UTC))


def build_adapters(config: EngineConfig, home: EngineHome) -> list[Adapter]:
    enabled = set(config.radar.enabled)
    adapters: list[Adapter] = []
    if "rss" in enabled:
        adapters.extend(RssAdapter(name, url) for name, url in config.radar.feeds.items())
    if "trends" in enabled:
        adapters.append(RssAdapter("google_trends", config.radar.trends_feed, kind="attention"))
    if "calendar" in enabled:
        adapters.append(CalendarAdapter(home.calendar_path, config.radar.lookahead_days))
    adapters.extend(
        ListingAdapter(name, spec, config.radar.max_items)
        for name, spec in config.radar.listings.items()
        if name in enabled
    )
    return adapters


class RadarStage:
    name = "radar"
    output_name = "signals"
    output_type = RadarResult

    def describe(self, output: RadarResult) -> str:
        return f"{len(output.signals)} signals · {len(output.errors)} sources failed"

    async def run(self, ctx: RunContext, inp: None) -> RadarResult:
        since = datetime.now(UTC) - timedelta(hours=ctx.config.radar.since_hours)
        return await run_radar(build_adapters(ctx.config, ctx.home), ctx, since)
