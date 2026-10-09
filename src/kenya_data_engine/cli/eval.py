"""`engine eval`: run the acceptance scenarios and compare each with its previous run."""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from kenya_data_engine import __version__
from kenya_data_engine.cli import research as research_cli
from kenya_data_engine.cli.common import State, _scrub, get_state, guarded
from kenya_data_engine.cli.research import require_search_key
from kenya_data_engine.cli.run import require_llm_key
from kenya_data_engine.cli.ui import console, err_console
from kenya_data_engine.context import open_context
from kenya_data_engine.data.periods import today_nairobi
from kenya_data_engine.errors import ConfigError
from kenya_data_engine.research.dossier import write_dossier
from kenya_data_engine.research.eval import (
    EvalRow,
    EvalStore,
    Scenario,
    judge,
    load_scenarios,
)
from kenya_data_engine.research.orchestrator import load_book, research
from kenya_data_engine.research.planner import TopicInput
from kenya_data_engine.runs import RunStore


@dataclass
class Scored:
    scenario: Scenario
    row: EvalRow | None
    previous: EvalRow | None
    failures: list[str]
    error: str | None = None


async def run_scenario(state: State, scenario: Scenario, preset: str | None) -> EvalRow:
    handle = RunStore(state.home.runs_dir).new_run(datetime.now())
    async with open_context(state.home, handle) as rc:
        outcome = await research(
            TopicInput(title=scenario.topic, summary=scenario.topic),
            rc,
            preset=preset,
            models=research_cli.default_models(),
        )
        write_dossier(outcome, rc, load_book(rc), today=today_nairobi())
    failures = judge(scenario, outcome.verdict, outcome.scorecard)
    return EvalRow(
        ts=datetime.now(UTC),
        engine_version=__version__,
        scenario=scenario.name,
        scorecard=outcome.scorecard,
        verdict=outcome.verdict,
        passed=not failures,
    )


def _delta(now: float, before: float | None, fmt: str) -> Text:
    shown = format(now, fmt)
    if before is None:
        return Text(shown)
    diff = now - before
    mark = "" if abs(diff) < 1e-9 else f" ({'+' if diff > 0 else '-'}{format(abs(diff), fmt)})"
    return Text(shown + mark, style="muted" if not mark else "")


def comparison_table(results: list[Scored]) -> Table:
    table = Table(
        title="Eval vs previous run",
        title_justify="left",
        title_style="accent",
        box=box.ROUNDED,
        border_style="muted",
    )
    left = ("Scenario", "Verdict", "Was", "Result")
    for col in ("Scenario", "Verdict", "Was", "Facts", "Cost $", "Credits", "Result"):
        table.add_column(
            col,
            overflow="fold",
            no_wrap=col in ("Scenario", "Verdict", "Was"),
            justify="left" if col in left else "right",
        )
    for r in results:
        if r.row is None:
            err = Text(f"error: {r.error}", style="fail")
            table.add_row(r.scenario.name, "-", "-", "-", "-", "-", err)
            continue
        sc, prev = r.row.scorecard, r.previous.scorecard if r.previous else None
        result = (
            Text("pass", style="ok")
            if r.row.passed
            else Text("FAIL: " + "; ".join(r.failures), style="fail")
        )
        table.add_row(
            r.scenario.name,
            r.row.verdict,
            r.previous.verdict if r.previous else "-",
            _delta(sc.facts, prev.facts if prev else None, "d"),
            _delta(sc.usd_spent, prev.usd_spent if prev else None, ".3f"),
            _delta(sc.credits_spent, prev.credits_spent if prev else None, "d"),
            result,
        )
    return table


@guarded
def eval_cmd(
    ctx: typer.Context,
    only: Annotated[
        str | None, typer.Option("--only", metavar="NAME", help="Run one scenario by name.")
    ] = None,
    budget: Annotated[
        str | None,
        typer.Option(
            "--budget", metavar="lean|standard|deep", help="Budget preset for every scenario."
        ),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Print results as JSON.")] = False,
) -> None:
    """Run the acceptance scenarios and compare each with its previous run.

    Scenarios live in the packaged eval.yaml (add or override with <home>/eval.yaml). Every run
    spends real money and search credits, and each result is recorded in the engine database.

    \b
    Examples:
      engine eval
      engine eval --only weak --budget lean
    """
    state = get_state(ctx)
    state.home.ensure()
    require_llm_key(state)
    require_search_key(state.home)
    scenarios = load_scenarios(state.home)
    if only is not None:
        scenarios = [s for s in scenarios if s.name == only]
        if not scenarios:
            names = ", ".join(s.name for s in load_scenarios(state.home))
            raise ConfigError(f"no scenario named {only!r}", hint=f"scenarios: {names}")
    store = EvalStore(state.home.db_path)
    out = err_console if json_out else console
    results: list[Scored] = []
    for scenario in scenarios:
        if not state.quiet:
            out.print(Text(f"… {scenario.name}: {scenario.topic}", style="accent"))
        previous = store.previous(scenario.name)
        try:
            row = asyncio.run(run_scenario(state, scenario, budget))
        except Exception as exc:  # one broken scenario must not hide the others
            msg = getattr(exc, "message", None) or str(exc) or type(exc).__name__
            msg = _scrub(msg, state.home)
            results.append(Scored(scenario, None, previous, [], error=msg))
            continue
        store.add(row)
        results.append(Scored(scenario, row, previous, judge(scenario, row.verdict, row.scorecard)))
    if json_out:
        typer.echo(
            json.dumps(
                [
                    {
                        "scenario": r.scenario.name,
                        "verdict": r.row.verdict if r.row else None,
                        "passed": bool(r.row and r.row.passed),
                        "failures": r.failures,
                        "error": r.error,
                        "scorecard": r.row.scorecard.model_dump(mode="json") if r.row else None,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
    else:
        console.print(comparison_table(results))
    if not all(r.row and r.row.passed for r in results):
        raise typer.Exit(1)
