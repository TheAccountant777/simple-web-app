"""`engine compare`: which topics survive across several runs, and how steady their scores are."""

import json
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from kenya_data_engine.cli.common import get_state, guarded
from kenya_data_engine.cli.ui import console
from kenya_data_engine.compare import ConsensusTopic, consensus, load_topic_runs
from kenya_data_engine.runs import RunStore

STABILITY_STYLE = {"strong": "ok", "mixed": "warn", "noise": "muted"}
NEED_TWO = "Compare needs at least 2 runs with topics — run `engine run` a few times first."


def score_range(c: ConsensusTopic) -> str:
    return f"{c.min_score:.1f}-{c.max_score:.1f}"


def consensus_table(topics: list[ConsensusTopic]) -> Table:
    table = Table(
        title="Topic consensus", title_justify="left", title_style="accent", box=box.ROUNDED
    )
    for name in ("#", "Topic", "Category", "Seen", "Mean", "Range", "Rank", "Stability"):
        table.add_column(
            name, justify="left" if name in ("Topic", "Category", "Stability") else "right"
        )
    for n, c in enumerate(topics, 1):
        table.add_row(
            str(n),
            c.title,
            c.category,
            f"{c.appearances}/{c.runs}",
            f"{c.mean_score:.2f}",
            score_range(c),
            f"{c.mean_rank:.1f}",
            Text(c.stability, style=STABILITY_STYLE[c.stability]),
        )
    return table


def criteria_table(c: ConsensusTopic) -> Table:
    table = Table(title=c.title, title_justify="left", title_style="accent", box=box.SIMPLE)
    table.add_column("Criterion")
    table.add_column("Mean", justify="right")
    table.add_column("Range", justify="right")
    for name, stat in c.criteria.items():
        rng = f"{stat.min}" if stat.min == stat.max else f"{stat.min}-{stat.max}"
        table.add_row(name, f"{stat.mean:.1f}", rng)
    return table


@guarded
def compare(
    ctx: typer.Context,
    run_ids: Annotated[
        list[str] | None, typer.Argument(help="Runs to compare (default: the last N).")
    ] = None,
    last: Annotated[
        int, typer.Option("--last", min=2, metavar="N", help="Compare the last N runs.")
    ] = 3,
    json_out: Annotated[bool, typer.Option("--json", help="Print the consensus as JSON.")] = False,
) -> None:
    """Match topics across runs and show how often and how consistently they come back.

    \b
    Examples:
      engine compare
      engine compare --last 5
      engine compare 2026-10-09-0800 2026-10-09-0900 --json
    """
    state = get_state(ctx)
    store = RunStore(state.home.runs_dir)
    ids = [store.open(r).run_id for r in run_ids] if run_ids else store.list()[:last]
    runs, skipped = load_topic_runs(store, ids)
    notes = Console(stderr=True) if json_out else console  # keep stdout pure JSON under --json
    if skipped:
        notes.print(f"[warn]skipped (no topics, incomplete): {', '.join(sorted(skipped))}[/]")
    if len(runs) < 2:
        if json_out:
            typer.echo("[]")
        notes.print(NEED_TWO)
        return
    topics = consensus(runs)
    if json_out:
        typer.echo(json.dumps([t.model_dump(mode="json") for t in topics], indent=2))
        return
    console.print(f"[muted]{len(runs)} runs: {', '.join(r for r, _ in runs)}[/]")
    console.print(consensus_table(topics))
    for c in topics[:3]:
        console.print(criteria_table(c))
