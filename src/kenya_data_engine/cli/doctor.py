"""`engine doctor`: health checks for keys, the LLM, search and every source."""

import asyncio
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, TypeAdapter
from rich.table import Table
from rich.text import Text

from kenya_data_engine.cli.common import State, get_state, guarded
from kenya_data_engine.cli.ui import badge, console, err_console
from kenya_data_engine.context import RunContext, open_context
from kenya_data_engine.radar.base import build_adapters
from kenya_data_engine.runs import RunHandle
from kenya_data_engine.tools.search import build_search

Group = Literal["keys", "llm", "search", "sources"]
Status = Literal["ok", "warn", "fail"]

GROUP_TITLES: dict[str, str] = {
    "keys": "API keys",
    "llm": "LLM",
    "search": "Search",
    "sources": "Radar sources",
}
CHECK_TIMEOUT_S = 30.0
_NO_KEY = "DEEPSEEK_API_KEY not set: run `engine init`"


class Check(BaseModel):
    name: str
    group: Group
    status: Status
    latency_ms: int | None = None
    detail: str = ""


def _key_checks(ctx: RunContext) -> list[Check]:
    s = ctx.secrets
    out: list[Check] = []
    if s.deepseek_api_key is None:
        out.append(Check(name="DeepSeek key", group="keys", status="fail", detail=_NO_KEY))
    else:
        out.append(Check(name="DeepSeek key", group="keys", status="ok", detail="set"))
    present = [
        n for n, v in (("Tavily", s.tavily_api_key), ("Serper", s.serper_api_key)) if v is not None
    ]
    if not present:
        out.append(
            Check(
                name="Search key",
                group="keys",
                status="fail",
                detail="no Tavily or Serper key: run `engine init`",
            )
        )
    elif len(present) == 1:
        out.append(
            Check(
                name="Search key",
                group="keys",
                status="warn",
                detail=f"only {present[0]} is set; add a second key for fallback (`engine init`)",
            )
        )
    else:
        out.append(Check(name="Search key", group="keys", status="ok", detail="Tavily + Serper"))
    return out


async def _timed(
    ctx: RunContext,
    name: str,
    group: Group,
    fn: Callable[[], Awaitable[tuple[Status, str]]],
) -> Check:
    """Run one probe; never raises. The probe returns (status, detail)."""
    start = time.perf_counter()
    try:
        status, detail = await asyncio.wait_for(fn(), timeout=CHECK_TIMEOUT_S)
    except TimeoutError:
        status, detail = "fail", f"timed out after {CHECK_TIMEOUT_S:.0f}s"
    except Exception as exc:
        status, detail = "fail", str(exc) or type(exc).__name__
        hint = getattr(exc, "hint", None)
        if hint:
            detail = f"{detail} ({hint})"
    ms = int((time.perf_counter() - start) * 1000)
    return Check(
        name=name, group=group, status=status, latency_ms=ms, detail=ctx.tracer.redact(detail)
    )


async def _llm_probe(ctx: RunContext) -> tuple[Status, str]:
    key = ctx.secrets.deepseek_api_key
    if key is None:
        return "fail", "skipped: no DeepSeek key"
    base = ctx.config.llm.base_url.rstrip("/")
    resp = await ctx.http.get(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key.get_secret_value()}"},
        timeout=15.0,
    )
    if resp.status_code in (401, 403):
        return "fail", f"key rejected (HTTP {resp.status_code}): run `engine init`"
    if resp.status_code != 200:
        return "fail", f"HTTP {resp.status_code} from {base}/models"
    try:
        listed = {m.get("id") for m in resp.json().get("data", [])}
    except (ValueError, AttributeError):
        return "fail", "unexpected response from /models"
    wanted = {s.model for s in ctx.config.llm.stages.values()}
    missing = sorted(wanted - listed)
    if missing:
        return "warn", f"model not listed: {', '.join(missing)}"
    return "ok", f"model {', '.join(sorted(wanted))} available"


async def _search_probe(ctx: RunContext) -> tuple[Status, str]:
    results = await build_search(ctx).search("Kenya inflation", n=1)
    if not results:
        return "warn", "search worked but returned no results"
    return "ok", f"{len(results)} result: {results[0].title[:50]}"


def _source_probe(ctx: RunContext, adapter: object) -> Callable[[], Awaitable[tuple[Status, str]]]:
    async def probe() -> tuple[Status, str]:
        since = datetime.now(UTC) - timedelta(days=30)
        signals = await adapter.fetch(ctx, since)  # type: ignore[attr-defined]
        if not signals:
            return "warn", "0 signals in the last 30 days (feed empty or selectors stale?)"
        return "ok", f"{len(signals)} signals"

    return probe


async def run_checks(ctx: RunContext) -> list[Check]:
    """Run every check concurrently. Individual checks never raise."""
    coros: list[Awaitable[Check]] = [
        _timed(ctx, "DeepSeek /models", "llm", lambda: _llm_probe(ctx)),
        _timed(ctx, "Web search", "search", lambda: _search_probe(ctx)),
    ]
    for adapter in build_adapters(ctx.config, ctx.home):
        coros.append(_timed(ctx, adapter.name, "sources", _source_probe(ctx, adapter)))
    return [*_key_checks(ctx), *await asyncio.gather(*coros)]


def checks_table(checks: list[Check]) -> Table:
    table = Table(box=None, header_style="bold", pad_edge=False, show_edge=False)
    table.add_column("Status", no_wrap=True)
    table.add_column("Check", no_wrap=True)
    table.add_column("Time", justify="right", style="muted", no_wrap=True)
    table.add_column("Detail", overflow="fold")
    first = True
    for group, title in GROUP_TITLES.items():
        rows = [c for c in checks if c.group == group]
        if not rows:
            continue
        if not first:
            table.add_row()
        first = False
        table.add_row("", Text(title, style="accent bold"), "", "")
        for c in rows:
            ms = "" if c.latency_ms is None else f"{c.latency_ms} ms"
            table.add_row(badge(c.status), c.name, ms, c.detail)
    return table


def summary_line(checks: list[Check]) -> Text:
    n = {s: sum(c.status == s for c in checks) for s in ("ok", "warn", "fail")}
    line = Text()
    line.append(f"{n['ok']} ok", style="ok")
    line.append(" · ", style="muted")
    line.append(f"{n['warn']} warn", style="warn" if n["warn"] else "muted")
    line.append(" · ", style="muted")
    line.append(f"{n['fail']} fail", style="fail" if n["fail"] else "muted")
    return line


async def _collect(state: State) -> list[Check]:
    state.home.ensure()
    with tempfile.TemporaryDirectory() as tmp:  # doctor leaves no run behind
        run = RunHandle("doctor", Path(tmp))
        async with open_context(state.home, run) as ctx:
            return await run_checks(ctx)


def run_doctor(state: State, *, json_out: bool = False) -> int:
    """Run and print the checks; return the process exit code (1 if any check failed)."""
    if json_out or state.quiet:
        checks = asyncio.run(_collect(state))
    else:
        with err_console.status("[accent]Running checks…[/]"):
            checks = asyncio.run(_collect(state))
    if json_out:
        typer.echo(TypeAdapter(list[Check]).dump_json(checks, indent=2).decode())
    else:
        console.print()
        console.print(checks_table(checks))
        console.print()
        console.print(summary_line(checks))
    return 1 if any(c.status == "fail" for c in checks) else 0


@guarded
def doctor(
    ctx: typer.Context,
    json_out: Annotated[
        bool, typer.Option("--json", help="Print the checks as JSON on stdout.")
    ] = False,
) -> None:
    """Check API keys, the LLM, web search and every Radar source.

    Exits 1 if any check fails, so it works in scripts.

    \b
    Examples:
      engine doctor
      engine doctor --json
    """
    code = run_doctor(get_state(ctx), json_out=json_out)
    if code:
        raise typer.Exit(code)
