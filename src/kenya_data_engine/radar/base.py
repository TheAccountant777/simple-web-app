"""Radar core: adapter protocol, dedupe, concurrent runner, stage."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

from kenya_data_engine.config import EngineConfig, load_sources
from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import ConfigError, EngineError, FetchError
from kenya_data_engine.health import HealthStore
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


async def fetch_with_timeout(adapter: Adapter, ctx: RunContext, since: datetime) -> list[Signal]:
    """Run one source under `radar.source_timeout_s`; a hang becomes a FetchError."""
    limit = ctx.config.radar.source_timeout_s
    try:
        return await asyncio.wait_for(adapter.fetch(ctx, since), timeout=limit)
    except TimeoutError as exc:
        raise FetchError(
            f"{adapter.name}: timed out after {limit:g}s",
            hint="the site is slow or unreachable; raise radar.source_timeout_s or disable it",
        ) from exc


def record_health(ctx: RunContext, name: str, signals: int | None, error: str | None) -> int:
    """Update a source's health row; returns its consecutive failures. Never raises."""
    try:
        store = HealthStore(ctx.home.db_path)
        if error is None:
            store.record_ok(name, signals or 0)
            return 0
        return store.record_failure(name, ctx.tracer.redact(error))
    except Exception:
        return 0  # health is bookkeeping; it must never break a run


async def run_radar(adapters: list[Adapter], ctx: RunContext, since: datetime) -> RadarResult:
    async def one(adapter: Adapter) -> list[Signal] | AdapterError:
        try:
            async with ctx.tracer.span("radar", "tool", adapter.name):
                return await fetch_with_timeout(adapter, ctx, since)
        except Exception as exc:
            return AdapterError(
                adapter=adapter.name, message=ctx.tracer.redact(str(exc) or type(exc).__name__)
            )

    results = await asyncio.gather(*(one(a) for a in adapters))
    signals: list[Signal] = []
    errors: list[AdapterError] = []
    for adapter, r in zip(adapters, results, strict=True):
        if isinstance(r, AdapterError):
            errors.append(r)
            record_health(ctx, adapter.name, None, r.message)
        else:
            signals.extend(r)
            record_health(ctx, adapter.name, len(r), None)
    unique = dedupe(signals)
    unique.sort(
        key=lambda s: (
            s.published_at is None,
            -(s.published_at.timestamp() if s.published_at else 0),
        )
    )
    return RadarResult(signals=unique, errors=errors, collected_at=datetime.now(UTC))


class BrokenAdapter:
    """Stands in for a source whose sources.yaml entry is invalid: it fails, the run goes on."""

    def __init__(self, name: str, problem: str) -> None:
        self.name = name
        self.problem = problem

    async def fetch(self, ctx: RunContext, since: datetime) -> list[Signal]:
        raise ConfigError(
            f"invalid source `{self.name}`: {self.problem}",
            hint="fix or remove it in <home>/sources.yaml",
        )


def build_adapters(config: EngineConfig, home: EngineHome) -> list[Adapter]:
    """One adapter per enabled source in sources.yaml, plus the calendar."""
    sources = load_sources(home)
    adapters: list[Adapter] = [CalendarAdapter(home.calendar_path, config.radar.lookahead_days)]
    adapters.extend(BrokenAdapter(n, why) for n, why in sources.invalid.items())
    for name, spec in sources.specs.items():
        if not spec.enabled:
            continue
        if spec.type == "rss":
            adapters.append(RssAdapter(name, spec.url, spec.kind))
        else:
            try:
                adapters.append(ListingAdapter(name, spec, config.radar.max_items))
            except EngineError as exc:  # unreachable for validated specs; stay robust anyway
                adapters.append(BrokenAdapter(name, exc.message))
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
