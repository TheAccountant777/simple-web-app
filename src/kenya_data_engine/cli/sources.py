"""`engine sources`: list and live-test Radar sources (built for humans and audit agents)."""

import asyncio
import dataclasses
import json
import time
from datetime import UTC, datetime
from typing import Annotated, Literal

import typer
from pydantic import BaseModel
from rich.table import Table
from rich.text import Text

from kenya_data_engine.cache import NoCache
from kenya_data_engine.cli.common import State, get_state, guarded, probe_context
from kenya_data_engine.cli.ui import badge, console
from kenya_data_engine.config import load_sources
from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import EngineError
from kenya_data_engine.health import HealthStore, failing_label
from kenya_data_engine.radar.base import Adapter, build_adapters, fetch_with_timeout, record_health

sources_app = typer.Typer(
    name="sources",
    help="List and live-test Radar sources.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

SHOWN_ITEMS = 5
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class SourceRow(BaseModel):
    name: str
    type: str | None
    kind: str | None
    enabled: bool
    url: str | None
    valid: bool
    error: str | None
    health: Literal["ok", "failing", "never_run", "invalid"]
    consecutive_failures: int
    last_ok_at: datetime | None
    last_error: str | None
    last_signal_count: int | None


class TestedItem(BaseModel):
    title: str
    date: datetime | None
    url: str | None


class SourceTest(BaseModel):
    __test__ = False  # not a pytest class

    name: str
    status: Literal["ok", "empty", "fail"]
    latency_ms: int
    item_count: int
    items: list[TestedItem]
    error: str | None
    hint: str | None


def source_rows(state: State) -> list[SourceRow]:
    sources = load_sources(state.home)
    health = HealthStore(state.home.db_path).all()
    rows: list[SourceRow] = []
    for name, spec in sources.specs.items():
        h = health.get(name)
        status: Literal["ok", "failing", "never_run", "invalid"] = "never_run"
        if h is not None:
            status = "failing" if h.consecutive_failures else "ok"
        rows.append(
            SourceRow(
                name=name,
                type=spec.type,
                kind=spec.kind,
                enabled=spec.enabled,
                url=spec.url,
                valid=True,
                error=None,
                health=status,
                consecutive_failures=h.consecutive_failures if h else 0,
                last_ok_at=h.last_ok_at if h else None,
                last_error=h.last_error if h else None,
                last_signal_count=h.last_signal_count if h else None,
            )
        )
    for name, problem in sources.invalid.items():
        rows.append(
            SourceRow(
                name=name,
                type=None,
                kind=None,
                enabled=False,
                url=None,
                valid=False,
                error=problem,
                health="invalid",
                consecutive_failures=0,
                last_ok_at=None,
                last_error=None,
                last_signal_count=None,
            )
        )
    return rows


def _health_text(row: SourceRow) -> Text:
    if row.health == "ok":
        return Text("ok", style="ok")
    if row.health == "failing":
        return Text(failing_label(row.consecutive_failures), style="fail")
    if row.health == "invalid":
        return Text("invalid", style="fail")
    return Text("never run", style="muted")


async def test_one(adapter: Adapter, ctx: RunContext) -> SourceTest:
    start = time.perf_counter()
    items: list[TestedItem] = []
    error = hint = None
    count = 0
    try:
        signals = await fetch_with_timeout(adapter, ctx, _EPOCH)
        count = len(signals)
        items = [
            TestedItem(title=s.title, date=s.published_at, url=s.url) for s in signals[:SHOWN_ITEMS]
        ]
    except Exception as exc:
        error = ctx.tracer.redact(str(exc) or type(exc).__name__)
        hint = getattr(exc, "hint", None)
    record_health(ctx, adapter.name, None if error else count, error)
    status: Literal["ok", "empty", "fail"] = "fail" if error else ("ok" if count else "empty")
    return SourceTest(
        name=adapter.name,
        status=status,
        latency_ms=int((time.perf_counter() - start) * 1000),
        item_count=count,
        items=items,
        error=error,
        hint=hint,
    )


async def test_sources(state: State, names: list[str] | None) -> list[SourceTest]:
    """Live-fetch (no cache) the named sources, or every enabled one when `names` is None."""
    async with probe_context(state) as ctx:
        live = dataclasses.replace(ctx, cache=NoCache())
        known = {r.name for r in source_rows(state)}
        adapters = [
            a
            for a in build_adapters(live.config, live.home, include_disabled=names is not None)
            if a.name in known and (names is None or a.name in names)
        ]
        return list(await asyncio.gather(*(test_one(a, live) for a in adapters)))


def _print_test(res: SourceTest) -> None:
    head = Text.assemble(
        badge("ok" if res.status == "ok" else "warn" if res.status == "empty" else "fail"),
        "  ",
        (res.name, "bold"),
        (f"  {res.latency_ms} ms", "muted"),
    )
    if res.status != "fail":
        head.append(f"  {res.item_count} item{'' if res.item_count == 1 else 's'}", style="muted")
    console.print(head)
    if res.error:
        console.print(Text(f"    {res.error}", style="fail"))
        if res.hint:
            console.print(Text(f"    → {res.hint}", style="muted"))
    elif res.status == "empty":
        console.print(
            Text("    0 items: the feed is empty or the selectors are stale", style="warn")
        )
    for item in res.items:
        when = item.date.date().isoformat() if item.date else "no date"
        console.print(Text(f"    {item.title[:70]} | {when} | {item.url or '-'}", style="muted"))


@guarded
def list_sources(
    ctx: typer.Context,
    json_out: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    """List every source with its type, kind, enabled flag and health.

    \b
    Examples:
      engine sources list
      engine sources list --json
    """
    rows = source_rows(get_state(ctx))
    if json_out:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in rows], indent=2))
        return
    table = Table(box=None, header_style="bold", pad_edge=False, show_edge=False)
    for col in ("Name", "Type", "Kind", "Enabled", "Health"):
        table.add_column(col, no_wrap=True)
    for r in rows:
        table.add_row(
            r.name, r.type or "?", r.kind or "?", "yes" if r.enabled else "no", _health_text(r)
        )
    console.print()
    console.print(table)
    console.print()
    console.print("[muted]Check one with `engine sources test <name>`.[/]")


@guarded
def test_command(
    ctx: typer.Context,
    name: Annotated[
        str | None, typer.Argument(help="Source name (see `engine sources list`).")
    ] = None,
    all_sources: Annotated[bool, typer.Option("--all", help="Test every enabled source.")] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    """Fetch a source live (cache bypassed) and show what was extracted.

    Shows status, latency, item count and the first 5 items (title | date | link), or the
    exact error and a hint. Exits 1 unless every tested source returned items.

    \b
    Examples:
      engine sources test cbk_news
      engine sources test --all --json
    """
    state = get_state(ctx)
    if (name is None) == (not all_sources):
        raise EngineError(
            "give a source name or --all, not both",
            hint="engine sources test <name>   or   engine sources test --all",
        )
    if name is not None:
        known = [r.name for r in source_rows(state)]
        if name not in known:
            raise EngineError(
                f"unknown source `{name}`",
                hint=f"run `engine sources list`; known: {', '.join(known)}",
            )
    results = asyncio.run(test_sources(state, None if name is None else [name]))
    if json_out:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
    else:
        console.print()
        for res in results:
            _print_test(res)
        ok = sum(r.status == "ok" for r in results)
        console.print(f"\n[muted]{ok}/{len(results)} sources ok[/]")
    if any(r.status != "ok" for r in results):
        raise typer.Exit(1)


sources_app.command("list")(list_sources)
sources_app.command("test")(test_command)
