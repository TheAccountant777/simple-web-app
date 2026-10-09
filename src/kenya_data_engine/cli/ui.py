"""Shared Rich look and feel: theme, console, tables, progress and footer."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from kenya_data_engine.context import StageEvent
from kenya_data_engine.models import TopicList
from kenya_data_engine.runs import RunHandle
from kenya_data_engine.trace import Tracer

THEME = Theme(
    {
        "ok": "green",
        "warn": "yellow",
        "fail": "bold red",
        "muted": "dim",
        "accent": "cyan",
        "score": "bold magenta",
    }
)

console = Console(theme=THEME, highlight=False)
err_console = Console(theme=THEME, stderr=True, highlight=False)

_BADGES = {
    "ok": ("✓ ok", "ok"),
    "warn": ("! warn", "warn"),
    "fail": ("✗ fail", "fail"),
    "skip": ("↷ skipped", "muted"),
    "done": ("✓ done", "ok"),
    "error": ("✗ failed", "fail"),
    "running": ("… running", "accent"),
}


def badge(status: str) -> Text:
    label, style = _BADGES.get(status, (status, "muted"))
    return Text(label, style=style)


def show_error(message: str, hint: str | None = None, *, stderr: bool = False) -> None:
    out = err_console if stderr else console
    out.print(Text(f"✗ {message}", style="fail"))
    if hint:
        out.print(Text(f"→ {hint}", style="muted"))


def _score_style(score: float) -> str:
    if score >= 4.0:
        return "ok"
    if score >= 3.0:
        return "warn"
    return "muted"


def topics_table(tl: TopicList) -> Table:
    table = Table(
        title="Ranked topics",
        title_style="accent",
        header_style="bold",
        title_justify="left",
        expand=False,
        box=box.ROUNDED,
        border_style="muted",
    )
    table.add_column("#", justify="right", style="muted", no_wrap=True)
    table.add_column("Topic", overflow="fold", min_width=18, ratio=3)
    table.add_column("Category", style="accent", overflow="fold")
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_column("Why now", overflow="fold", ratio=3)
    table.add_column("Signals", justify="right", style="muted", no_wrap=True)
    for i, t in enumerate(tl.topics, 1):
        table.add_row(
            str(i),
            Text(t.title, style="bold"),
            t.category.replace("_", " "),
            Text(f"{t.final_score:.2f}", style=_score_style(t.final_score)),
            t.why_now,
            str(len(t.signal_ids)),
        )
    return table


class _StatusColumn(SpinnerColumn):
    """Spinner while a stage runs, then its final mark."""

    def render(self, task: Task) -> Text:
        state = task.fields.get("state", "running")
        if state == "running":
            return super().render(task)  # type: ignore[return-value]
        return badge(state)


_Emit = Callable[[StageEvent], None]


@contextmanager
def stage_progress(quiet: bool = False) -> Iterator[_Emit]:
    """Yield an `emit` callback that drives one progress row per stage (on stderr)."""
    if quiet:
        yield lambda event: None
        return
    progress = Progress(
        _StatusColumn(style="accent"),
        TextColumn("[accent]{task.fields[stage]:<12}[/]"),
        TimeElapsedColumn(),
        TextColumn("[muted]{task.fields[detail]}[/]"),
        console=err_console,
        transient=False,
    )
    tasks: dict[str, TaskID] = {}

    def emit(event: StageEvent) -> None:
        tid = tasks.get(event.stage)
        if event.status == "start" or tid is None:
            tid = progress.add_task("", total=None, stage=event.stage, state="running", detail="")
            tasks[event.stage] = tid
        if event.status == "start":
            return
        state = {"skip": "skip", "done": "done", "error": "error"}[event.status]
        progress.update(tid, state=state, detail=event.detail[:80])
        progress.stop_task(tid)

    with progress:
        yield emit


def run_footer(run: RunHandle, tracer: Tracer, elapsed_s: float | None = None) -> Panel:
    summary = tracer.summary()
    errors = int(summary["errors"])
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="muted")
    grid.add_column()
    grid.add_row("run", Text(run.run_id, style="accent"))
    grid.add_row(
        "cost",
        Text(
            f"${tracer.total_cost:.4f} of ${tracer.run_budget_usd:.2f} budget",
            style="warn" if tracer.total_cost >= tracer.run_budget_usd else "",
        ),
    )
    if elapsed_s is not None:
        grid.add_row("time", f"{elapsed_s:.1f}s")
    grid.add_row("errors", Text(str(errors), style="fail" if errors else "ok"))
    grid.add_row("artifacts", Text(str(run.dir), overflow="fold"))
    return Panel(grid, title="Run summary", title_align="left", border_style="muted", expand=False)
