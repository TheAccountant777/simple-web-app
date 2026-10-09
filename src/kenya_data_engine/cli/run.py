"""`engine run`: the full pipeline."""

import asyncio
import time
from datetime import datetime
from typing import Annotated, Any

import typer
from rich.markup import escape
from rich.panel import Panel

from kenya_data_engine.cli.common import State, get_state, guarded
from kenya_data_engine.cli.ui import console, err_console, run_footer, stage_progress, topics_table
from kenya_data_engine.config import load_secrets
from kenya_data_engine.context import open_context
from kenya_data_engine.errors import ConfigError
from kenya_data_engine.models import RadarResult, TopicList
from kenya_data_engine.pipeline import Stage, run_pipeline
from kenya_data_engine.radar.base import RadarStage
from kenya_data_engine.runs import RunStore
from kenya_data_engine.synth.stage import SynthesizeStage


def build_stages() -> list[Stage[Any, Any]]:
    """The default pipeline (a seam the tests replace with fakes)."""
    return [RadarStage(), SynthesizeStage()]


def require_llm_key(state: State) -> None:
    if load_secrets(state.home).deepseek_api_key is None:
        raise ConfigError("DEEPSEEK_API_KEY not set", hint="run `engine init`")


@guarded
def run(
    ctx: typer.Context,
    top: Annotated[
        int | None,
        typer.Option(
            "--top", min=1, metavar="N", help="How many topics to keep (default: config)."
        ),
    ] = None,
    since: Annotated[
        int | None,
        typer.Option(
            "--since", min=1, metavar="HOURS", help="Only look at signals from the last N hours."
        ),
    ] = None,
    resume: Annotated[
        str | None,
        typer.Option("--resume", metavar="RUN_ID", help="Continue a run, reusing finished stages."),
    ] = None,
    json_out: Annotated[
        bool, typer.Option("--json", help="Print the topics as JSON on stdout.")
    ] = False,
) -> None:
    """Collect Kenyan signals, cluster and score them, and print the ranked topics.

    \b
    Examples:
      engine run
      engine run --top 3 --since 24
      engine run --resume 2026-10-09-0800
      engine run --json > topics.json
    """
    state = get_state(ctx)
    state.home.ensure()
    require_llm_key(state)
    store = RunStore(state.home.runs_dir)
    handle = store.open(resume) if resume else store.new_run(datetime.now())
    started = time.perf_counter()

    async def go() -> tuple[TopicList, RadarResult | None, Panel]:
        with stage_progress(quiet=state.quiet) as emit:
            async with open_context(state.home, handle, emit) as rc:
                if top is not None:
                    rc.config.top_n = top
                if since is not None:
                    rc.config.radar.since_hours = since
                result = await run_pipeline(build_stages(), rc, resume=resume is not None)
                if not isinstance(result, TopicList):
                    raise TypeError("pipeline did not end with a topic list")
                radar = handle.read("signals", RadarResult)
                return result, radar, run_footer(handle, rc.tracer, time.perf_counter() - started)

    topics, radar, footer = asyncio.run(go())

    out = err_console if json_out else console  # keep stdout pure JSON under --json
    if json_out:
        typer.echo(topics.model_dump_json(indent=2))
    else:
        console.print()
        if topics.topics:
            console.print(topics_table(topics))
        else:
            console.print("[warn]No topics came out of this run.[/]")
    if topics.budget_exhausted:
        out.print(
            "[warn]! Budget exhausted: some clusters were not scored. "
            "Raise budgets.run_usd in config.yaml to score them all.[/]"
        )
    for note in topics.dropped:
        out.print(f"[warn]! dropped:[/] [muted]{escape(note)}[/]")
    if radar is not None:
        for err in radar.errors:
            out.print(f"[warn]! source {err.adapter}:[/] [muted]{escape(err.message)}[/]")
    if not state.quiet and not json_out:
        console.print(footer)
