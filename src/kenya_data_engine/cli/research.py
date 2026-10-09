"""`engine research`: take one topic from the radar (or free text) to a dossier."""

import asyncio
import json
import re
from datetime import datetime
from typing import Annotated, Any

import typer
from pydantic_ai.models import Model
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kenya_data_engine.cli.common import _scrub, get_state, guarded
from kenya_data_engine.cli.run import require_llm_key
from kenya_data_engine.cli.ui import console, err_console, show_error
from kenya_data_engine.config import load_secrets
from kenya_data_engine.context import open_context
from kenya_data_engine.data.periods import today_nairobi
from kenya_data_engine.errors import ConfigError, EngineError
from kenya_data_engine.home import EngineHome
from kenya_data_engine.models import RadarResult, TopicList
from kenya_data_engine.research.budget import LedgerSnapshot
from kenya_data_engine.research.dossier import write_dossier
from kenya_data_engine.research.models import Claim, NeedStatus
from kenya_data_engine.research.orchestrator import (
    ResearchOutcome,
    load_book,
    load_topic,
    research,
)
from kenya_data_engine.research.planner import TopicInput
from kenya_data_engine.runs import RunHandle, RunStore

MAX_SIGNALS = 15
_RUN_TOPIC = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{4}(?:-\d+)?):(.+)$")
_PRESETS = ("lean", "standard", "deep")


def default_models() -> dict[str, Model] | None:
    """The model seam: tests replace this to run the whole command on FunctionModels."""
    return None


# --- topic resolution ---------------------------------------------------------------------------


def _topics_of(run: RunHandle) -> TopicList | None:
    return run.read("topics", TopicList)


def _build(run: RunHandle, topic: Any) -> TopicInput:
    radar = run.read("signals", RadarResult)
    by_id = {s.id: s for s in radar.signals} if radar else {}
    signals = []
    for sid in topic.signal_ids:
        sig = by_id.get(sid)
        if sig is not None:
            signals.append(f"{sig.title} — {sig.url}" if sig.url else sig.title)
    return TopicInput(
        title=topic.title,
        summary=topic.summary,
        why_now=topic.why_now,
        signals=signals[:MAX_SIGNALS],
    )


def resolve_topic(arg: str, store: RunStore) -> TopicInput:
    """A rank in the latest run's topics, `<run-id>:<topic-id>`, or free text as the title."""
    text = arg.strip()
    if not text:
        raise ConfigError("the topic is empty", hint="give a rank, `<run-id>:<topic-id>` or text")
    if text.isdigit():
        for run_id in store.list():
            run = store.open(run_id)
            topics = _topics_of(run)
            if topics is None:
                continue
            rank = int(text)
            if not 1 <= rank <= len(topics.topics):
                valid = f"1-{len(topics.topics)}" if topics.topics else "none"
                raise ConfigError(
                    f"no topic ranked {rank} in run {run_id}",
                    hint=f"valid ranks: {valid}",
                )
            return _build(run, topics.topics[rank - 1])
        raise ConfigError("no run with ranked topics yet", hint="run `engine run` first")
    m = _RUN_TOPIC.match(text)
    if m:
        run = store.open(m[1])
        topics = _topics_of(run)
        if topics is None:
            raise ConfigError(f"run {m[1]} has no topics", hint="pick a run made by `engine run`")
        for topic in topics.topics:
            if topic.id == m[2]:
                return _build(run, topic)
        ids = ", ".join(t.id for t in topics.topics) or "none"
        raise ConfigError(f"no topic {m[2]!r} in run {m[1]}", hint=f"topic ids: {ids}")
    return TopicInput(title=text, summary=text)


def require_search_key(home: EngineHome) -> None:
    secrets = load_secrets(home)
    if secrets.tavily_api_key is None and secrets.serper_api_key is None:
        raise ConfigError(
            "no search key set (TAVILY_API_KEY or SERPER_API_KEY)", hint="run `engine init`"
        )


# --- live progress ------------------------------------------------------------------------------

_STATUS_STYLE = {
    "satisfied": "ok",
    "partial": "warn",
    "not_found": "fail",
    "fact": "ok",
    "inference": "warn",
    "speculation": "muted",
    "refused": "fail",
}
_STEP_MARK = {
    "start": ("…", "accent"),
    "done": ("✓", "ok"),
    "resumed": ("↷", "muted"),
    "skipped": ("↷", "muted"),
    "stopped": ("!", "warn"),
}


class LivePrinter:
    """One Rich line per step, need and claim, plus a budget line after each finished step."""

    def __init__(self, out: Console, quiet: bool = False) -> None:
        self.out, self.quiet = out, quiet
        self._after_done = False

    def _line(self, *parts: tuple[str, str]) -> None:
        text = Text.assemble(*parts)
        text.no_wrap = True
        text.overflow = "ellipsis"
        self.out.print(text)

    def __call__(self, kind: str, payload: Any) -> None:
        if self.quiet:
            return
        if kind == "step":
            status = payload["status"]
            mark, style = _STEP_MARK.get(status, ("·", "muted"))
            if payload["step"] == "outcome":
                self._after_done = False
                return
            detail = payload["detail"]
            if status in ("resumed", "skipped", "stopped"):
                detail = f"{status}: {detail}" if detail else status
            self._line(
                (f"{mark} ", style),
                (f"{payload['step']:<10}", "bold"),
                (detail, "muted" if status == "start" else ""),
            )
            self._after_done = status in ("done", "stopped")
        elif kind == "need" and isinstance(payload, NeedStatus):
            style = _STATUS_STYLE.get(payload.status, "")
            note = (
                f"  {payload.notes[0]}" if payload.notes and payload.status != "satisfied" else ""
            )
            self._line(
                ("    ", ""),
                (f"{payload.need_id:<3}", "accent"),
                (f"{payload.status:<10}", style),
                (f" {payload.points} pts, round {payload.round}", "muted"),
                (note, "muted"),
            )
        elif kind == "claim" and isinstance(payload, Claim):
            style = _STATUS_STYLE.get(payload.status, "")
            self._line(
                ("    ", ""),
                (f"{payload.id:<4}", "accent"),
                (f"{payload.status:<11}", style),
                (" " + " ".join(payload.text.split()), ""),
            )
        elif kind == "ledger" and isinstance(payload, LedgerSnapshot) and self._after_done:
            self._after_done = False
            self._line(
                (
                    f"    budget ${payload.usd_spent:.3f} of ${payload.usd_total:.2f} · "
                    f"{payload.credits_spent} of {payload.credits_total} searches · "
                    f"{payload.elapsed_s:.0f}s of {payload.seconds_total:.0f}s",
                    "muted",
                )
            )


# --- final report -------------------------------------------------------------------------------

_VERDICT_STYLE = {"supported": "ok", "reframed": "warn", "reject": "fail"}


def outcome_panel(outcome: ResearchOutcome) -> Panel:
    sc = outcome.scorecard
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="muted", no_wrap=True)
    grid.add_column(overflow="fold")
    grid.add_row("verdict", Text(outcome.verdict, style=_VERDICT_STYLE.get(outcome.verdict, "")))
    for reason in outcome.verdict_reasons[:4]:
        grid.add_row("", Text(reason))
    grid.add_row(
        "claims",
        f"{sc.facts} facts · {sc.inferences} inferences · {sc.refused} refused"
        + (f" · {sc.speculation} speculation" if sc.speculation else ""),
    )
    grid.add_row("needs", f"{sc.needs_satisfied} of {sc.needs_total} satisfied")
    grid.add_row(
        "cost",
        f"${sc.usd_spent:.4f} of ${sc.usd_cap:.2f} · {sc.credits_spent} of {sc.credits_cap} "
        f"searches · {sc.seconds:.0f}s",
    )
    grid.add_row("stopped", Text(sc.stopped_because or "-"))
    for gap in outcome.gaps[:3]:
        grid.add_row("gap", Text(gap))
    if outcome.challenge_note:
        grid.add_row("challenge", Text(outcome.challenge_note))
    grid.add_row("run", Text(outcome.run_id, style="accent"))
    return Panel(grid, title="Research", title_align="left", border_style="muted", expand=False)


def outcome_json(outcome: ResearchOutcome, dossier: Any) -> str:
    return json.dumps(
        {
            "run_id": outcome.run_id,
            "dossier": str(dossier) if dossier else None,
            "topic": outcome.brief.topic,
            "verdict": outcome.verdict,
            "verdict_reasons": outcome.verdict_reasons,
            "stopped_because": outcome.stopped_because,
            "scorecard": outcome.scorecard.model_dump(mode="json"),
            "gaps": outcome.gaps,
            "challenge_note": outcome.challenge_note,
            "claims": [
                {"id": c.id, "status": c.status, "text": c.text, "tier": c.tier}
                for c in outcome.claims
            ],
        },
        indent=2,
    )


@guarded
def research_cmd(
    ctx: typer.Context,
    topic: Annotated[
        str | None,
        typer.Argument(
            metavar="[TOPIC]",
            help="A rank from the latest `engine run`, `<run-id>:<topic-id>`, or free text.",
        ),
    ] = None,
    budget: Annotated[
        str | None,
        typer.Option(
            "--budget", metavar="lean|standard|deep", help="Budget preset (default: config)."
        ),
    ] = None,
    plan_only: Annotated[
        bool, typer.Option("--plan-only", help="Stop after the plan; write no dossier.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Research even if the planner rejects the topic.")
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume", metavar="RUN_ID", help="Continue a research run (TOPIC optional)."
        ),
    ] = None,
    json_out: Annotated[
        bool, typer.Option("--json", help="Print the outcome as JSON on stdout.")
    ] = False,
) -> None:
    """Research one topic: plan, find data, check every claim, write a dossier.

    \b
    Examples:
      engine research 1                       the top topic from the latest `engine run`
      engine research "Kenya fuel prices and VAT" --budget lean
      engine research 2 --plan-only           see the plan and the verdict, spend little
      engine research --resume 2026-10-09-1530
      engine research 1 --json > outcome.json

    Exit code 0 means a dossier was written (a rejected or partial one counts); 1 means an error.
    """
    state = get_state(ctx)
    out = err_console if json_out else console
    try:
        state.home.ensure()
        require_llm_key(state)
        require_search_key(state.home)
        if budget is not None and budget not in _PRESETS:
            raise ConfigError(
                f"unknown budget {budget!r}", hint=f"choose one of: {', '.join(_PRESETS)}"
            )
        store = RunStore(state.home.runs_dir)
        if resume:
            handle = store.open(resume)
            topic_in = resolve_topic(topic, store) if topic else load_topic(handle)
            if topic_in is None:
                raise ConfigError(
                    f"run {resume} has no saved research topic", hint="give the TOPIC again"
                )
        else:
            if not topic:
                raise ConfigError("give a TOPIC", hint="a rank from `engine run`, or free text")
            topic_in = resolve_topic(topic, store)
            handle = store.new_run(datetime.now())
        if not json_out and not state.quiet:
            out.print(Text(f"Researching: {topic_in.title}", style="accent"))
            out.print(Text(f"run {handle.run_id}", style="muted"))

        async def go() -> tuple[ResearchOutcome, Any]:
            async with open_context(state.home, handle) as rc:
                outcome = await research(
                    topic_in,
                    rc,
                    preset=budget,
                    plan_only=plan_only,
                    force=force,
                    resume=resume is not None,
                    models=default_models(),
                    on_event=LivePrinter(out, quiet=state.quiet),
                )
                path = None
                if not plan_only:
                    path = write_dossier(outcome, rc, load_book(rc), today=today_nairobi())
                return outcome, path

        outcome, path = asyncio.run(go())
    except EngineError as exc:  # this command exits 1 on any error
        show_error(_scrub(exc.message, state.home), exc.hint, stderr=json_out)
        raise typer.Exit(1) from None
    if json_out:
        typer.echo(outcome_json(outcome, path))
    if not state.quiet:
        out.print()
        out.print(outcome_panel(outcome))
        # a plain line, so the path can be selected and copied whole
        out.print(
            Text(f"dossier: {path}" if path else "no dossier written (--plan-only)"), soft_wrap=True
        )
        if plan_only:
            out.print(plan_summary(outcome))


def plan_summary(outcome: ResearchOutcome) -> Group:
    brief = outcome.brief
    table = Table(title="Data needs", title_justify="left", title_style="accent", box=box.ROUNDED)
    for col in ("Id", "Kind", "Pri", "Need"):
        table.add_column(col, overflow="fold")
    for n in brief.data_needs:
        table.add_row(n.id, n.kind, str(n.priority), n.question)
    return Group(Text(f"Core question: {brief.core_question}", style="bold"), table)
