"""`engine catalog probe`: live-check catalog entries, disabled ones included."""

import asyncio
import dataclasses
import json
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

import typer
from pydantic import BaseModel
from rich.text import Text

from kenya_data_engine.cache import NoCache
from kenya_data_engine.cli.common import State, get_state, guarded, probe_context
from kenya_data_engine.cli.ui import badge, console
from kenya_data_engine.context import RunContext
from kenya_data_engine.data.adapters import ADAPTERS
from kenya_data_engine.data.adapters.base import FETCH_LOG, policy_fetch
from kenya_data_engine.data.registry import CatalogEntry, get_entry, load_catalog
from kenya_data_engine.errors import EngineError
from kenya_data_engine.tools.urlpolicy import sniff

catalog_app = typer.Typer(
    name="catalog",
    help="Check catalog entries against the live sites.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

MAX_SAMPLE_BYTES = 10 * 1024 * 1024
MAX_SAMPLES_PER_KEY = 2
SHOWN_LINKS = 3
_EXT = {
    "pdf": ".pdf",
    "xlsx": ".xlsx",
    "xls": ".xls",
    "html": ".html",
    "csv": ".csv",
    "json": ".json",
}


class ProbeResult(BaseModel):
    key: str
    adapter: str
    enabled: bool
    status: Literal["ok", "empty", "error"]
    final_url: str | None = None
    links_found: int = 0
    links: list[str] = []
    sniffed: str | None = None
    saved: list[str] = []
    error: str | None = None
    hint: str | None = None


def _filename(url: str, index: int, kind: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", unquote(urlsplit(url).path.rsplit("/", 1)[-1])).strip("._")
    name = name or f"item-{index}"
    ext = _EXT.get(kind, "")
    return name if ext and name.lower().endswith(ext) else name + ext


async def probe_entry(entry: CatalogEntry, ctx: RunContext, save_dir: Path | None) -> ProbeResult:
    res = ProbeResult(key=entry.key, adapter=entry.adapter, enabled=entry.enabled, status="error")
    log: list[str] = []
    token = FETCH_LOG.set(log)
    try:
        items = await ADAPTERS[entry.adapter].discover(entry, ctx)
        res.final_url = log[0] if log else None
        res.links_found = len(items)
        res.links = [i.url for i in items[:SHOWN_LINKS]]
        if not items:
            res.status = "empty"
            return res
        for n, item in enumerate(items[:MAX_SAMPLES_PER_KEY]):
            if n and save_dir is None:
                break
            page = await policy_fetch(item.url, ctx, "item")
            kind = sniff(page.content)
            if n == 0:
                res.sniffed = kind
                res.final_url = res.final_url or page.url
            if save_dir is not None and len(page.content) <= MAX_SAMPLE_BYTES:
                target = save_dir / entry.key.replace("/", "_") / _filename(item.url, n, kind)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(page.content)
                res.saved.append(str(target))
        res.status = "ok"
    except Exception as exc:
        res.error = ctx.tracer.redact(str(exc) or type(exc).__name__)
        res.hint = getattr(exc, "hint", None)
    finally:
        FETCH_LOG.reset(token)
    return res


async def probe(state: State, keys: list[str], save_dir: Path | None) -> list[ProbeResult]:
    async with probe_context(state) as ctx:
        live = dataclasses.replace(ctx, cache=NoCache())
        entries = [get_entry(state.home, k) for k in keys]
        results = [await probe_entry(e, live, save_dir) for e in entries]
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / "probe.json").write_text(
            json.dumps([r.model_dump(mode="json") for r in results], indent=2), encoding="utf-8"
        )
    return results


def _print(r: ProbeResult) -> None:
    kind = {"ok": "ok", "empty": "warn", "error": "fail"}[r.status]
    flag = "" if r.enabled else "  (disabled)"
    console.print(Text.assemble(badge(kind), "  ", (r.key, "bold"), (flag, "muted")))
    if r.final_url:
        console.print(Text(f"    url: {r.final_url}", style="muted"), soft_wrap=True)
    if r.status != "error":
        console.print(
            Text(f"    {r.links_found} link(s), first item: {r.sniffed or '-'}", style="muted")
        )
    for link in r.links:
        console.print(Text(f"      {link}", style="muted"), soft_wrap=True)
    for path in r.saved:
        console.print(Text(f"    saved {path}", style="muted"), soft_wrap=True)
    if r.error:
        console.print(Text(f"    {r.error}", style="fail"), soft_wrap=True)
        if r.hint:
            console.print(Text(f"    → {r.hint}", style="muted"), soft_wrap=True)


@guarded
def probe_command(
    ctx: typer.Context,
    keys: Annotated[list[str] | None, typer.Argument(help="Series keys to probe.")] = None,
    all_entries: Annotated[bool, typer.Option("--all", help="Probe every entry.")] = False,
    save_samples: Annotated[
        Path | None,
        typer.Option("--save-samples", help="Save up to 2 items per key (and probe.json) here."),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    """Run discovery live (cache bypassed) on catalog entries, disabled ones included.

    Shows the status, final URL, links found (first 3) and the sniffed type of the first item.
    Diagnostic only: writes no series rows and exits 0 even when entries fail.

    \b
    Examples:
      engine catalog probe wb:FP.CPI.TOTL.ZG
      engine catalog probe --all --save-samples ./samples
    """
    state = get_state(ctx)
    if bool(keys) == all_entries:
        raise EngineError(
            "give series keys or --all, not both",
            hint="engine catalog probe <key>...   or   engine catalog probe --all",
        )
    chosen = list(load_catalog(state.home)) if all_entries else list(keys or [])
    results = asyncio.run(probe(state, chosen, save_samples))
    if json_out:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
        return
    console.print()
    for r in results:
        _print(r)
    ok = sum(r.status == "ok" for r in results)
    console.print(f"\n[muted]{ok}/{len(results)} entries ok[/]")


catalog_app.command("probe")(probe_command)
