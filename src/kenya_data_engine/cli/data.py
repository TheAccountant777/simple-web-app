"""`engine data`: list the series catalog, fetch a series, show stored values."""

import asyncio
import json
import sys
from typing import Annotated
from urllib.parse import urlsplit

import typer
from rich.table import Table
from rich.text import Text

from kenya_data_engine.cli.common import State, get_state, guarded, probe_context
from kenya_data_engine.cli.ui import badge, console
from kenya_data_engine.data.csvsafe import write_csv
from kenya_data_engine.data.registry import FetchOutcome, fetch_series, get_entry, load_catalog
from kenya_data_engine.data.store import SeriesStore

data_app = typer.Typer(
    name="data",
    help="The data warehouse: list, fetch and show series.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@guarded
def list_series(
    ctx: typer.Context,
    json_out: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    """List catalog series with adapter, tier, enabled flag and rows stored.

    \b
    Examples:
      engine data list
      engine data list --json
    """
    state = get_state(ctx)
    store = SeriesStore(state.home.db_path)
    rows = [
        {
            "key": e.key,
            "title": e.title,
            "adapter": e.adapter,
            "tier": e.tier,
            "enabled": e.enabled,
            "note": e.note,
            "rows": len(store.latest(e.key)) if e.spec else 0,
        }
        for e in load_catalog(state.home).values()
    ]
    if json_out:
        typer.echo(json.dumps(rows, indent=2))
        return
    table = Table(box=None, header_style="bold", pad_edge=False, show_edge=False)
    for col in ("Key", "Title", "Adapter", "Tier", "Enabled", "Rows"):
        table.add_column(col, no_wrap=col != "Title")
    for r in rows:
        table.add_row(
            Text(str(r["key"])),
            Text(str(r["title"])),
            Text(str(r["adapter"])),
            Text(str(r["tier"])),
            Text("yes" if r["enabled"] else "no"),
            Text(str(r["rows"])),
        )
    console.print()
    console.print(table)
    console.print()
    console.print("[muted]Fetch one with `engine data fetch <key>`.[/]")


async def _fetch(state: State, key: str, limit: int) -> FetchOutcome:
    async with probe_context(state) as ctx:
        return await fetch_series(key, ctx, limit=limit)


def _print_outcome(out: FetchOutcome) -> None:
    bad = bool(out.error) or (out.report is not None and out.report.status == "quarantined")
    console.print()
    console.print(Text.assemble(badge("fail" if bad else "ok"), "  ", (out.key, "bold")))
    added = out.added
    console.print(
        f"    discovered {out.discovered}, fetched {out.fetched}"
        + (
            f", new {added.new}, unchanged {added.unchanged}, revised {added.revised}"
            if added
            else ""
        )
    )
    if out.report:
        style = "ok" if out.report.status == "accepted" else "fail"
        console.print(
            Text(f"    checks: {out.report.status} ({out.report.checked} checked)", style=style)
        )
        for w in out.report.warnings:
            console.print(Text(f"      ! {w}", style="warn"), soft_wrap=True)
    for it in out.items:
        if it.status == "quarantined":
            console.print(Text(f"    quarantined {it.final_url or it.url}", style="fail"))
            for f in it.report.failures if it.report else []:
                console.print(Text(f"      ✗ {f}", style="fail"), soft_wrap=True)
        elif it.status == "error":
            console.print(Text(f"    error {it.url}: {it.error}", style="fail"), soft_wrap=True)
    if out.error and not out.items:  # discovery failed before any item
        console.print(Text(f"    error: {out.error}", style="fail"), soft_wrap=True)


@guarded
def fetch_command(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Series key (see `engine data list`).")],
    limit: Annotated[int, typer.Option("--limit", min=1, help="Newest items to fetch.")] = 12,
    json_out: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    """Fetch a series: discover, download, extract, check, store.

    Exits 1 on an error or a quarantined table (the blob is kept, no rows are stored).

    \b
    Examples:
      engine data fetch wb:FP.CPI.TOTL.ZG
      engine data fetch epra.pump_prices --limit 3 --json
    """
    out = asyncio.run(_fetch(get_state(ctx), key, limit))
    if json_out:
        typer.echo(out.model_dump_json(indent=2))
    else:
        _print_outcome(out)
    if out.error or (out.report is not None and out.report.status == "quarantined"):
        raise typer.Exit(1)


@guarded
def show(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Series key (see `engine data list`).")],
    entity: Annotated[str | None, typer.Option("--entity", help="Only this entity.")] = None,
    last: Annotated[int | None, typer.Option("--last", min=1, help="Only the last N rows.")] = None,
    csv_out: Annotated[bool, typer.Option("--csv", help="Write CSV to stdout.")] = False,
) -> None:
    """Show the latest vintage of a series with its source host.

    \b
    Examples:
      engine data show wb:PA.NUS.FCRF --last 5
      engine data show epra.pump_prices --entity Nairobi --csv
    """
    state = get_state(ctx)
    get_entry(state.home, key)  # unknown keys are an error, empty series are not
    rows = SeriesStore(state.home.db_path).latest(key, entity)
    if last is not None:
        rows = rows[-last:]
    header = ["period", "entity", "metric", "value", "unit", "revised", "source"]
    body = [
        [
            r.period.label,
            r.entity,
            r.metric,
            str(r.value),
            r.unit,
            "revised" if r.revised else "",
            urlsplit(r.provenance.url).hostname or "",
        ]
        for r in rows
    ]
    if csv_out:
        sys.stdout.write(write_csv(body, header))
        return
    if not body:
        console.print(
            Text(f"No rows stored for {key}. Run `engine data fetch {key}`.", style="muted")
        )
        return
    table = Table(box=None, header_style="bold", pad_edge=False, show_edge=False)
    for col in header:
        table.add_column(col, no_wrap=True, justify="right" if col == "value" else "left")
    for row in body:
        table.add_row(*(Text(c) for c in row))
    console.print()
    console.print(table)


data_app.command("list")(list_series)
data_app.command("fetch")(fetch_command)
data_app.command("show")(show)
