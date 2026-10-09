"""`engine stage`: run a single stage on its own."""

import asyncio
import time
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from kenya_data_engine.cli.common import get_state, guarded
from kenya_data_engine.cli.run import require_llm_key
from kenya_data_engine.cli.ui import console, run_footer, stage_progress
from kenya_data_engine.context import RunContext, open_context
from kenya_data_engine.errors import EngineError
from kenya_data_engine.models import RadarResult
from kenya_data_engine.pipeline import Stage, run_pipeline
from kenya_data_engine.radar.base import RadarStage
from kenya_data_engine.runs import RunStore
from kenya_data_engine.synth.stage import SynthesizeStage


class StageName(StrEnum):
    radar = "radar"
    synthesize = "synthesize"


DOWNSTREAM: dict[StageName, tuple[str, ...]] = {StageName.radar: ("topics",)}


class _Preloaded:
    """Feeds an already-collected RadarResult into the next stage."""

    name = "input"
    output_name = "signals"
    output_type = RadarResult

    def __init__(self, data: RadarResult) -> None:
        self.data = data

    async def run(self, ctx: RunContext, inp: None) -> RadarResult:
        return self.data


def _load_input(path: Path) -> RadarResult:
    try:
        return RadarResult.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EngineError(f"cannot read {path}", hint=str(exc)) from exc
    except ValueError as exc:
        raise EngineError(
            f"{path} is not a valid signals file",
            hint="use a signals.json written by `engine stage radar`",
        ) from exc


@guarded
def stage(
    ctx: typer.Context,
    name: Annotated[StageName, typer.Argument(metavar="STAGE", help="radar or synthesize.")],
    run_id: Annotated[
        str | None,
        typer.Option("--run", metavar="RUN_ID", help="Use (or add to) an existing run."),
    ] = None,
    input_path: Annotated[
        Path | None,
        typer.Option("--input", help="JSON file with the stage input (signals.json)."),
    ] = None,
) -> None:
    """Run one stage on its own and write its artifact.

    \b
    Examples:
      engine stage radar
      engine stage synthesize --run 2026-10-09-0800
      engine stage synthesize --input ./signals.json
    """
    state = get_state(ctx)
    if input_path is not None and run_id is not None:
        raise EngineError(
            "--input and --run cannot be used together",
            hint="use --run to continue an existing run, or --input for a standalone file",
        )
    state.home.ensure()
    store = RunStore(state.home.runs_dir)
    stages: list[Stage[Any, Any]] = []
    if name is StageName.radar:
        stages = [RadarStage()]
        artifact = "signals"
    else:
        data: RadarResult | None = None
        if input_path is not None:
            data = _load_input(input_path)
        elif run_id is not None:
            data = store.open(run_id).read("signals", RadarResult)
            if data is None:
                raise EngineError(
                    f"run {run_id} has no usable signals.json",
                    hint="run `engine stage radar --run <id>` first",
                )
        if data is None:
            raise EngineError(
                "synthesize needs --input or --run",
                hint="pass --input signals.json, or --run <id> of a run that has signals.json",
            )
        require_llm_key(state)
        stages = [_Preloaded(data), SynthesizeStage()]
        artifact = "topics"
    handle = store.open(run_id) if run_id else store.new_run(datetime.now())
    if run_id is not None:
        for stale in DOWNSTREAM.get(name, ()):  # later stages' artifacts are now out of date
            (handle.dir / f"{stale}.json").unlink(missing_ok=True)
    started = time.perf_counter()

    async def go() -> None:
        with stage_progress(quiet=state.quiet) as emit:
            async with open_context(state.home, handle, emit) as rc:
                await run_pipeline(stages, rc)
                if not state.quiet:
                    console.print(run_footer(handle, rc.tracer, time.perf_counter() - started))

    asyncio.run(go())
    console.print(f"[ok]✓[/] wrote [accent]{handle.dir / (artifact + '.json')}[/]", soft_wrap=True)
