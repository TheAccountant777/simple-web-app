"""`engine doctor`: health checks for keys, the LLM, search and every source."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, TypeAdapter
from rich.table import Table
from rich.text import Text

from kenya_data_engine.cli.common import State, get_state, guarded, probe_context
from kenya_data_engine.cli.ui import badge, console, err_console
from kenya_data_engine.config import legacy_radar_keys
from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import ConfigError
from kenya_data_engine.health import failing_label
from kenya_data_engine.radar.base import Adapter, build_adapters, fetch_with_timeout, record_health
from kenya_data_engine.tools.search import build_search

Group = Literal["keys", "llm", "search", "config", "sources"]
Status = Literal["ok", "warn", "fail", "skip"]

GROUP_TITLES: dict[str, str] = {
    "keys": "API keys",
    "llm": "LLM",
    "search": "Search",
    "config": "Config",
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


def _config_checks(ctx: RunContext) -> list[Check]:
    legacy = legacy_radar_keys(ctx.home)
    if not legacy:
        return []
    keys = ", ".join(f"radar.{k}" for k in legacy)
    return [
        Check(
            name="config.yaml",
            group="config",
            status="warn",
            detail=f"{keys} no longer apply: sources live in sources.yaml. Move entries "
            "there, or run `engine init --reset-config` to start from a clean config.yaml",
        )
    ]


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
        return "skip", "no DeepSeek key"
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
    try:
        search = build_search(ctx)
    except ConfigError:
        return "skip", "no search key"
    results = await search.search("Kenya inflation", n=1)
    if not results:
        return "warn", "search worked but returned no results"
    return "ok", f"{len(results)} result: {results[0].title[:50]}"


def _source_probe(ctx: RunContext, adapter: Adapter) -> Callable[[], Awaitable[tuple[Status, str]]]:
    async def probe() -> tuple[Status, str]:
        since = datetime.now(UTC) - timedelta(days=30)
        try:
            signals = await fetch_with_timeout(adapter, ctx, since)
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
            hint = getattr(exc, "hint", None)
            failing = record_health(ctx, adapter.name, None, detail)
            return "fail", f"{detail}{f' ({hint})' if hint else ''} · {failing_label(failing)}"
        record_health(ctx, adapter.name, len(signals), None)
        if not signals:
            return "warn", "0 signals in the last 30 days (feed empty or selectors stale?)"
        return "ok", f"{len(signals)} signal{'' if len(signals) == 1 else 's'}"

    return probe


async def run_checks(ctx: RunContext) -> list[Check]:
    """Run every check concurrently. Individual checks never raise."""
    coros: list[Awaitable[Check]] = [
        _timed(ctx, "DeepSeek /models", "llm", lambda: _llm_probe(ctx)),
        _timed(ctx, "Web search", "search", lambda: _search_probe(ctx)),
    ]
    for adapter in build_adapters(ctx.config, ctx.home):
        coros.append(_timed(ctx, adapter.name, "sources", _source_probe(ctx, adapter)))
    return [*_key_checks(ctx), *_config_checks(ctx), *await asyncio.gather(*coros)]


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
    n = {s: sum(c.status == s for c in checks) for s in ("ok", "warn", "fail", "skip")}
    line = Text()
    line.append(f"{n['ok']} ok", style="ok")
    line.append(" · ", style="muted")
    line.append(f"{n['warn']} warn", style="warn" if n["warn"] else "muted")
    line.append(" · ", style="muted")
    line.append(f"{n['fail']} fail", style="fail" if n["fail"] else "muted")
    line.append(" · ", style="muted")
    line.append(f"{n['skip']} skipped", style="muted")
    return line


async def _collect(state: State) -> list[Check]:
    async with probe_context(state) as ctx:  # doctor leaves no run behind
        return await run_checks(ctx)


def run_doctor(
    state: State, *, json_out: bool = False, blocking: frozenset[str] | None = None
) -> int:
    """Run and print the checks; return the exit code (1 if a check in `blocking` failed).

    `blocking` is the set of groups whose failures count (default: all of them). Failures
    in other groups are shown as a note that the run continues without them.
    """
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
    failed = [c for c in checks if c.status == "fail"]
    if blocking is not None:
        soft = [c for c in failed if c.group not in blocking]
        if soft and not json_out:
            names = ", ".join(c.name for c in soft)
            console.print(f"[muted]Note: failing: {names}; the run continues without them.[/]")
        failed = [c for c in failed if c.group in blocking]
    return 1 if failed else 0


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
