"""`engine report`: speed, cost and reliability numbers from past runs."""

import json
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kenya_data_engine.cli.common import get_state, guarded
from kenya_data_engine.cli.ui import badge, console
from kenya_data_engine.config import load_config
from kenya_data_engine.report import (
    Aggregate,
    RunMetrics,
    build_report,
    enabled_source_names,
    load_run_metrics,
)
from kenya_data_engine.research.dossier import DossierInfo, list_dossiers
from kenya_data_engine.runs import RunStore

_BARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    return "".join(_BARS[round((v - lo) / span * (len(_BARS) - 1))] for v in values)


def _secs(ms: int) -> str:
    return f"{ms / 1000:.1f}s"


def latency_label(ms: int | None) -> str:
    """Live mean latency; a source answered only from cache has none to show."""
    return "cached" if ms is None else _secs(ms)


def _table(title: str, *cols: str) -> Table:
    t = Table(title=title, title_justify="left", title_style="accent", box=box.ROUNDED)
    for i, c in enumerate(cols):
        t.add_column(c, justify="left" if i == 0 else "right", overflow="fold")
    return t


def run_view(m: RunMetrics) -> list[Panel | Table]:
    state = Text("complete", style="ok") if m.complete else Text("incomplete", style="warn")
    rate = "n/a" if m.cache_hit_rate is None else f"{m.cache_hit_rate:.0%}"
    head = Text.assemble(
        f"{m.run_id} · ",
        state,
        f" · {_secs(m.duration_ms)} · ${m.cost_usd:.4f} of ${m.budget_usd:.2f}\n",
        f"{m.topics} topics · {m.dropped} dropped · {m.errors} errors · "
        f"{m.http_requests} http, cache hit rate {rate}",
    )
    stages = _table("Stages", "Stage", "Status", "Duration", "Detail")
    for s in m.stages:
        stages.add_row(s.name, badge(s.status), _secs(s.duration_ms), s.detail)
    sources = _table("Sources (slowest first)", "Source", "Status", "Latency", "Signals", "Error")
    for src in sorted(m.sources, key=lambda x: x.latency_ms, reverse=True):
        sources.add_row(
            src.name,
            badge(src.status),
            "cached" if src.cached else _secs(src.latency_ms),
            "-" if src.signals is None else str(src.signals),
            src.error or "",
        )
    llm = _table("LLM", "Calls", "Tokens in", "Tokens out", "Cost", "Mean latency", "Errors")
    x = m.llm
    llm.add_row(
        str(x.calls),
        str(x.input_tokens),
        str(x.output_tokens),
        f"${x.cost_usd:.4f}",
        _secs(x.mean_latency_ms),
        str(x.errors),
    )
    return [
        Panel(head, title="Run", title_align="left", border_style="muted"),
        stages,
        sources,
        llm,
    ]


def aggregate_view(agg: Aggregate) -> list[Panel | Table | Text]:
    stages = _table("Stage timings", "Stage", "p50", "p95")
    for name, p50 in agg.stage_p50_ms.items():
        stages.add_row(name, _secs(p50), _secs(agg.stage_p95_ms[name]))
    sources = _table("Source reliability", "Source", "Success", "Live latency", "Cache")
    for name, rate in sorted(agg.source_success_rate.items(), key=lambda kv: kv[1]):
        share = agg.source_cache_share.get(name)
        sources.add_row(
            name,
            f"{rate:.0%}",
            latency_label(agg.source_mean_latency_ms.get(name)),
            "-" if share is None else f"{share:.0%}",
        )
    cost = [c for _, c in agg.cost_per_run]
    dur = [d for _, d in agg.duration_per_run]
    mean_rate = agg.mean_cache_hit_rate
    trend = Text.assemble(
        (f"Cost     {sparkline(cost)}  ", "accent"),
        f"${min(cost):.4f} - ${max(cost):.4f}\n" if cost else "\n",
        (f"Duration {sparkline(list(map(float, dur)))}  ", "accent"),
        f"{_secs(min(dur))} - {_secs(max(dur))}\n" if dur else "\n",
        f"Mean cache hit rate: {'n/a' if mean_rate is None else f'{mean_rate:.0%}'}",
    )
    return [
        Panel(
            trend,
            title=f"Last {agg.runs} runs (oldest first)",
            title_align="left",
            border_style="muted",
        ),
        stages,
        sources,
    ]


def research_view(rows: list[DossierInfo]) -> list[Panel | Table | Text]:
    """Cost per dossier, facts per dollar and gap rate for the latest dossiers."""
    table = _table("Research", "Dossier", "Verdict", "Facts", "Cost", "Facts/$", "Gap rate")
    for d in rows:
        per = "-" if d.facts_per_usd is None else f"{d.facts_per_usd:.1f}"
        table.add_row(
            f"{d.date.isoformat()}/{d.path.name}",
            d.verdict,
            str(d.facts),
            f"${d.usd:.4f}",
            per,
            f"{d.gap_rate:.0%}",
        )
    usd = sum(d.usd for d in rows)
    facts = sum(d.facts for d in rows)
    gap = sum(d.gap_rate for d in rows) / len(rows)
    total = Text(
        f"{len(rows)} dossiers · ${usd:.4f} in total · "
        + (f"{facts / usd:.1f} facts per dollar" if usd > 0 else "no cost recorded")
        + f" · mean gap rate {gap:.0%}",
        style="muted",
    )
    return [table, total]


@guarded
def report(
    ctx: typer.Context,
    run_id: Annotated[
        str | None, typer.Argument(help="One run to inspect (default: aggregate).")
    ] = None,
    last: Annotated[
        int, typer.Option("--last", min=1, metavar="N", help="Runs to aggregate.")
    ] = 10,
    json_out: Annotated[bool, typer.Option("--json", help="Print metrics as JSON.")] = False,
) -> None:
    """Speed, cost and reliability across runs (or the detail of one).

    \b
    Examples:
      engine report
      engine report 2026-10-09-0800
      engine report --last 5 --json
    """
    state = get_state(ctx)
    store = RunStore(state.home.runs_dir)
    ids = [run_id] if run_id else store.list()[:last]
    if not ids:
        if json_out:  # keep stdout machine-readable; the hint goes to stderr
            typer.echo(
                json.dumps(
                    {"runs": [], "aggregate": build_report([]).model_dump(mode="json")}, indent=2
                )
            )
            Console(stderr=True).print("No runs yet — run `engine run`.")
        else:
            console.print("No runs yet — run `engine run`.")
        return
    budget = load_config(state.home).budgets.run_usd
    dossiers = list_dossiers(state.home.briefs_dir)[:last]
    runs = [load_run_metrics(store.open(i), budget) for i in reversed(ids)]  # oldest first
    agg = build_report(runs, only=enabled_source_names(state.home))
    if json_out:
        payload = {
            "runs": [r.model_dump(mode="json") for r in runs],
            "aggregate": agg.model_dump(mode="json"),
        }
        if dossiers:  # the key appears only once there is research to report
            payload["research"] = [d.model_dump(mode="json") for d in dossiers]
        typer.echo(json.dumps(payload, indent=2))
        return
    for part in run_view(runs[0]) if run_id else aggregate_view(agg):
        console.print(part)
    if dossiers and not run_id:
        for part in research_view(dossiers):
            console.print(part)
