"""Live tab, "the movement": the pipeline running in real time.

A run executes in a Textual worker. Everything the engine reports (trace events through
`Tracer.subscribe`, stage events through `ctx.emit`) is turned into a message and posted to the
pane with `post_message`, which is safe from any thread or task; widgets are only ever touched in
the pane's own message handlers.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ProgressBar, RichLog, Static
from textual.worker import Worker

from kenya_data_engine.config import load_secrets
from kenya_data_engine.context import StageEvent, open_context
from kenya_data_engine.errors import ConfigError, EngineError
from kenya_data_engine.home import EngineHome
from kenya_data_engine.models import TopicList
from kenya_data_engine.pipeline import Stage, run_pipeline
from kenya_data_engine.radar.base import RadarStage
from kenya_data_engine.report import enabled_source_names
from kenya_data_engine.runs import RunStore
from kenya_data_engine.synth.stage import SynthesizeStage
from kenya_data_engine.trace import TraceEvent
from kenya_data_engine.tui.widgets import ACCENT, BAD, GOOD, INK, MUTED, WARN, Scrub

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
KIND_ICON = {"llm": "✦", "tool": "◇", "http": "↔", "stage": "▣"}
MAX_EVENT_LINES = 2000
TILE_WIDTH = 24
COMPACT_BELOW = 20  # pane height under which the layout drops to its 80x24 form
IDLE_HINT = "Press r to start a run — every trace event streams here."

StageState = Literal["idle", "running", "done", "error", "skipped"]
STATE_STYLE = {"idle": MUTED, "running": ACCENT, "done": GOOD, "error": BAD, "skipped": MUTED}


def default_stages() -> list[Stage[Any, Any]]:
    return [RadarStage(), SynthesizeStage()]


def fmt_secs(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def fmt_ms(ms: int) -> str:
    return f"{ms}ms" if ms < 1000 else fmt_secs(ms / 1000)


# ---- state (plain data; no widgets) -------------------------------------------------------


@dataclass
class StageView:
    name: str
    state: StageState = "idle"
    started: float | None = None
    duration: float | None = None
    detail: str = ""


@dataclass
class SourceTile:
    name: str
    state: Literal["pending", "ok", "error"] = "pending"
    latency_ms: int = 0
    signals: int | None = None
    cached: bool = False


@dataclass
class LiveState:
    budget: float
    stages: dict[str, StageView] = field(default_factory=dict)
    sources: dict[str, SourceTile] = field(default_factory=dict)
    cost: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    llm_calls: int = 0
    http: int = 0
    cache_hits: int = 0
    started: float | None = None
    finished: float | None = None

    @property
    def cache_rate(self) -> float | None:
        return self.cache_hits / self.http if self.http else None

    def elapsed(self, now: float | None = None) -> float:
        if self.started is None:
            return 0.0
        end = self.finished if self.finished is not None else (now or time.monotonic())
        return max(0.0, end - self.started)

    def apply_event(self, ev: TraceEvent) -> None:
        self.cost += ev.cost_usd
        if ev.kind == "llm":
            self.llm_calls += 1
            self.tokens_in += ev.input_tokens
            self.tokens_out += ev.output_tokens
        elif ev.kind == "http":
            self.http += 1
            self.cache_hits += bool(ev.attrs.get("from_cache"))
        elif ev.kind == "tool" and ev.stage == "radar":
            signals = ev.attrs.get("signals")
            calls, hits = ev.attrs.get("http"), ev.attrs.get("cache_hits")
            self.sources[ev.name] = SourceTile(
                name=ev.name,
                state="ok" if ev.status == "ok" else "error",
                latency_ms=ev.latency_ms,
                signals=signals if isinstance(signals, int) else None,
                cached=bool(calls) and calls == hits,
            )

    def apply_stage(self, ev: StageEvent, now: float) -> None:
        view = self.stages.setdefault(ev.stage, StageView(ev.stage))
        if ev.status == "start":
            view.state, view.started = "running", now
        elif ev.status == "skip":
            view.state, view.detail = "skipped", ev.detail
        else:
            view.state = "done" if ev.status == "done" else "error"
            view.duration = now - view.started if view.started is not None else None
            view.detail = ev.detail


# ---- pure rendering ------------------------------------------------------------------------


def stage_glyph(view: StageView, tick: int) -> str:
    return {
        "idle": "○",
        "running": SPINNER[tick % len(SPINNER)],
        "done": "✓",
        "error": "✗",
        "skipped": "↷",
    }[view.state]


def stage_status(view: StageView, now: float, tick: int) -> str:
    glyph = stage_glyph(view, tick)
    if view.state == "running" and view.started is not None:
        return f"{glyph} {fmt_secs(now - view.started)}"
    if view.state in ("done", "error") and view.duration is not None:
        return f"{glyph} {fmt_secs(view.duration)}"
    return f"{glyph} {'skipped' if view.state == 'skipped' else view.state}"


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text.ljust(width)
    return text[: max(width - 1, 0)] + "…"


def render_pipeline(
    stages: list[StageView], width: int, now: float, tick: int, compact: bool
) -> Text:
    out = Text(no_wrap=True, overflow="crop")
    if not stages:
        return out
    arrow = " ─▶ "
    box = max(12, (width - len(arrow) * (len(stages) - 1)) // len(stages))
    if compact:  # two lines: "glyph name time" and the detail, no frames
        top, bottom = Text(no_wrap=True), Text(no_wrap=True)
        for i, v in enumerate(stages):
            style = STATE_STYLE[v.state]
            head = _fit(f"{stage_status(v, now, tick)} {v.name}", box)
            top.append(head, style=f"bold {style}")
            bottom.append(_fit(v.detail, box), style=MUTED)
            if i < len(stages) - 1:
                top.append(arrow, style=MUTED)
                bottom.append(" " * len(arrow))
        out.append_text(top)
        out.append("\n")
        out.append_text(bottom)
        return out
    rows = [Text(no_wrap=True) for _ in range(4)]
    for i, v in enumerate(stages):
        style = STATE_STYLE[v.state]
        title = f" {v.name} "
        inner = box - 2
        rows[0].append("╭─" + title + "─" * max(inner - 1 - len(title), 0) + "╮", style=style)
        rows[1].append("│ ", style=style)
        rows[1].append(_fit(stage_status(v, now, tick), inner - 1), style=f"bold {style}")
        rows[1].append("│", style=style)
        rows[2].append("│ ", style=style)
        rows[2].append(_fit(v.detail, inner - 1), style=MUTED)
        rows[2].append("│", style=style)
        rows[3].append("╰" + "─" * inner + "╯", style=style)
        if i < len(stages) - 1:
            for r, joiner in zip(rows, ("    ", arrow, "    ", "    "), strict=True):
                r.append(joiner, style=MUTED)
    for n, r in enumerate(rows):
        out.append_text(r)
        if n < len(rows) - 1:
            out.append("\n")
    return out


def render_tile(tile: SourceTile) -> Text:
    t = Text(no_wrap=True)
    if tile.state == "pending":
        t.append("○ ", style=MUTED)
        t.append(_fit(tile.name, TILE_WIDTH - 3), style=MUTED)
        return t
    ok = tile.state == "ok"
    t.append("✓ " if ok else "✗ ", style=GOOD if ok else BAD)
    t.append(_fit(tile.name, 12), style=INK)
    t.append(" " + ("cache" if tile.cached else fmt_ms(tile.latency_ms)).rjust(5), style=MUTED)
    t.append(("" if tile.signals is None else f" {tile.signals}").ljust(4), style=ACCENT)
    return t


def render_sources(tiles: list[SourceTile], width: int) -> Text:
    out = Text(no_wrap=True)
    cols = max(1, (width + 1) // (TILE_WIDTH + 1))
    for i, tile in enumerate(tiles):
        cell = render_tile(tile)
        cell.truncate(TILE_WIDTH, pad=True)
        out.append_text(cell)
        out.append("\n" if (i + 1) % cols == 0 and i < len(tiles) - 1 else " ")
    return out


def format_event_line(ev: TraceEvent, scrub: Scrub, width: int) -> Text:
    ok = ev.status == "ok"
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append(ev.ts.astimezone().strftime("%H:%M:%S "), style=MUTED)
    line.append(KIND_ICON.get(ev.kind, "·") + " ", style=GOOD if ok else BAD)
    line.append(f"{ev.kind:<5} ", style=MUTED)
    line.append(f"{scrub(ev.name)[:22]:<22} ", style=INK)
    line.append(f"{fmt_ms(ev.latency_ms):>6}  ", style=MUTED)
    if ev.kind == "llm":
        line.append(
            f"{ev.input_tokens:,}→{ev.output_tokens:,} tok  ${ev.cost_usd:.4f}", style=ACCENT
        )
    elif ev.kind == "http":
        hit = bool(ev.attrs.get("from_cache"))
        line.append("cache hit" if hit else "cache miss", style=GOOD if hit else MUTED)
    elif ev.kind == "tool" and isinstance(ev.attrs.get("signals"), int):
        line.append(f"{ev.attrs['signals']} signals", style=ACCENT)
    if not ok:
        line.append("  ✗ " + " ".join(scrub(ev.error or "failed").split()), style=BAD)
    line.truncate(width, overflow="ellipsis")
    return line


# ---- messages (everything crossing from the worker into the UI) ------------------------------


class RunStarted(Message):
    def __init__(self, run_id: str, budget: float, expected: list[str]) -> None:
        super().__init__()
        self.run_id, self.budget, self.expected = run_id, budget, expected


class TraceArrived(Message):
    def __init__(self, event: TraceEvent) -> None:
        super().__init__()
        self.event = event


class StageArrived(Message):
    def __init__(self, event: StageEvent) -> None:
        super().__init__()
        self.event = event


class RunFinished(Message):
    def __init__(self, topics: int | None, dropped: int | None) -> None:
        super().__init__()
        self.topics, self.dropped = topics, dropped


class RunFailed(Message):
    def __init__(self, message: str, hint: str | None) -> None:
        super().__init__()
        self.message, self.hint = message, hint


class RunCancelled(Message):
    pass


# ---- widgets -------------------------------------------------------------------------------


class PipelineStrip(Widget):
    """One box per stage, joined by arrows."""

    def __init__(self, pane: "LivePane") -> None:
        super().__init__(id="pipeline")
        self.pane = pane

    def render(self) -> Text:
        p = self.pane
        return render_pipeline(
            list(p.state.stages.values()),
            self.size.width,
            time.monotonic(),
            p.tick,
            p.has_class("compact"),
        )


class SourceGrid(Static):
    def __init__(self, pane: "LivePane") -> None:
        super().__init__(id="source-grid")
        self.pane = pane

    def render(self) -> Text:
        # answered sources first so progress is visible without scrolling; stable within a group
        tiles = sorted(self.pane.state.sources.values(), key=lambda t: t.state == "pending")
        if not tiles:
            return Text("Sources appear here while a run is going.", style=MUTED)
        return render_sources(tiles, self.size.width)


# ---- the pane ------------------------------------------------------------------------------


class LivePane(Widget):
    BINDINGS = [
        Binding("r", "start", "Run"),
        Binding("x", "cancel", "Cancel"),
        Binding("enter", "open_run", "Open run"),
    ]

    def __init__(
        self,
        home: EngineHome,
        scrub: Scrub,
        stages_factory: Callable[[], list[Stage[Any, Any]]] | None = None,
    ) -> None:
        super().__init__(id="live-pane")
        self.home = home
        self.scrub = scrub
        self.stages_factory = stages_factory
        self.state = LiveState(budget=0.0)
        self.running = False
        self.tick = 0
        self.run_id: str | None = None
        self._worker: Worker[None] | None = None
        self._timer: Any = None
        self._finished_run: str | None = None

    # -- layout --
    def compose(self) -> ComposeResult:
        yield PipelineStrip(self)
        with Horizontal(id="live-mid"):
            with VerticalScroll(id="sources-box"):
                yield SourceGrid(self)
            with Vertical(id="gauges"):
                yield Static(id="g-cost")
                yield ProgressBar(total=1, show_eta=False, show_percentage=False, id="cost-bar")
                yield Static(id="g-rest")
        yield Static(id="live-result")
        yield RichLog(
            max_lines=MAX_EVENT_LINES, min_width=20, wrap=False, markup=False, id="events"
        )

    def on_mount(self) -> None:
        self.query_one("#sources-box").border_title = "Sources"
        self.query_one("#gauges").border_title = "Gauges"
        self.query_one("#events").border_title = "Event stream"
        self.query_one("#live-result").display = False
        self._show_idle_stages()
        self.query_one("#events", RichLog).write(Text(IDLE_HINT, style=MUTED))
        self._refresh_gauges()
        self._apply_compact()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_compact()

    def _apply_compact(self) -> None:
        self.set_class(0 < self.size.height < COMPACT_BELOW, "compact")
        self._refresh_gauges()

    def _show_idle_stages(self) -> None:
        names = ["radar", "synthesize"]
        if self.stages_factory is not None:
            with contextlib.suppress(Exception):  # a broken factory is reported at run start
                names = [s.name for s in self.stages_factory()]
        self.state.stages = {n: StageView(n) for n in names}

    # -- actions --
    def action_start(self) -> None:
        if self.running:
            self.notify("A run is already in progress — x cancels it", severity="warning")
            return
        self._reset()
        self.running = True
        self._timer = self.set_interval(0.1, self._on_tick)
        self._worker = self.run_worker(
            self._execute(), name="live-run", group="live", exit_on_error=False
        )

    def action_cancel(self) -> None:
        if self.running and self._worker is not None:
            self._worker.cancel()

    def action_open_run(self) -> None:
        if self._finished_run is None:
            self.notify("No finished run yet — press r to start one", timeout=3)
            return
        open_run = getattr(self.app, "open_run", None)
        if callable(open_run):
            open_run(self._finished_run)

    # -- the worker (never touches a widget) --
    async def _execute(self) -> None:
        try:
            if self.stages_factory is None and load_secrets(self.home).deepseek_api_key is None:
                raise ConfigError("DEEPSEEK_API_KEY not set", hint="run `engine init`")
            stages = (self.stages_factory or default_stages)()
            run = RunStore(self.home.runs_dir).new_run(datetime.now())

            def on_stage(e: StageEvent) -> None:
                self.post_message(StageArrived(e))

            def on_trace(e: TraceEvent) -> None:
                self.post_message(TraceArrived(e))

            async with open_context(self.home, run, on_stage) as rc:
                unsubscribe = rc.tracer.subscribe(on_trace)
                try:
                    expected = (
                        sorted(enabled_source_names(self.home) - {"calendar"})
                        if any(s.name == "radar" for s in stages)
                        else []
                    )
                    if expected:
                        expected.insert(0, "calendar")
                    self.post_message(
                        RunStarted(run.run_id, rc.config.budgets.run_usd, [s.name for s in stages])
                    )
                    self.post_message(TraceSources(expected))
                    result = await run_pipeline(stages, rc)
                finally:
                    unsubscribe()
            topics = result if isinstance(result, TopicList) else None
            self.post_message(
                RunFinished(
                    len(topics.topics) if topics else None, len(topics.dropped) if topics else None
                )
            )
        except asyncio.CancelledError:
            self.post_message(RunCancelled())
            raise
        except EngineError as exc:
            self.post_message(RunFailed(exc.message, exc.hint))
        except Exception as exc:
            self.post_message(
                RunFailed(str(exc) or type(exc).__name__, "see trace.jsonl in the run folder")
            )

    # -- message handlers (UI thread) --
    def on_run_started(self, msg: RunStarted) -> None:
        self.run_id = msg.run_id
        self.state.budget = msg.budget
        self.state.stages = {n: StageView(n) for n in msg.expected}
        self.state.started = time.monotonic()
        self._refresh_all()

    def on_trace_sources(self, msg: "TraceSources") -> None:
        for name in msg.names:
            self.state.sources.setdefault(name, SourceTile(name))
        self.query_one(SourceGrid).refresh(layout=True)

    def on_trace_arrived(self, msg: TraceArrived) -> None:
        self.state.apply_event(msg.event)
        self.write_event(msg.event)
        self._refresh_all()

    def on_stage_arrived(self, msg: StageArrived) -> None:
        self.state.apply_stage(msg.event, time.monotonic())
        self._refresh_all()

    def on_run_finished(self, msg: RunFinished) -> None:
        self._stop(f"{self.run_id}")
        self._finished_run = self.run_id
        s = self.state
        counts = f" · {msg.topics} topics · {msg.dropped} dropped" if msg.topics is not None else ""
        text = Text()
        text.append("✓ Run complete ", style=f"bold {GOOD}")
        text.append(f"{self.run_id}{counts}\n", style=INK)
        text.append(
            f"${s.cost:.4f} of ${s.budget:.2f} · {fmt_secs(s.elapsed())} · {s.llm_calls} LLM "
            f"calls · enter opens it in the Runs tab",
            style=MUTED,
        )
        self._show_result(text, GOOD)
        self._notify_app()

    def on_run_failed(self, msg: RunFailed) -> None:
        self._stop(None)
        self._finished_run = self.run_id
        text = Text()
        text.append("✗ Run failed  ", style=f"bold {BAD}")
        text.append(self.scrub(msg.message), style=INK)
        if msg.hint:
            text.append("\n→ " + self.scrub(msg.hint), style=WARN)
        self._show_result(text, BAD)
        self._notify_app()

    def on_run_cancelled(self, msg: RunCancelled) -> None:
        self._stop(None)
        self._finished_run = self.run_id
        for view in self.state.stages.values():
            if view.state == "running":
                view.state = "error"
        text = Text()
        text.append("■ Run cancelled  ", style=f"bold {WARN}")
        text.append(
            f"partial results kept in {self.run_id}; r starts a new run, enter opens this one",
            style=MUTED,
        )
        self._show_result(text, WARN)
        self._notify_app()

    # -- helpers --
    def write_event(self, ev: TraceEvent) -> None:
        log = self.query_one("#events", RichLog)
        width = max(log.size.width - 2, 20)
        log.write(format_event_line(ev, self.scrub, width), width=width)

    def _reset(self) -> None:
        self.state = LiveState(budget=self.state.budget)
        self.run_id = None
        self._finished_run = None
        self.query_one("#live-result").display = False
        log = self.query_one("#events", RichLog)
        log.clear()
        self._show_idle_stages()
        self._refresh_all()

    def _stop(self, _label: str | None) -> None:
        self.running = False
        self.state.finished = time.monotonic()
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._refresh_all()

    def _show_result(self, text: Text, colour: str) -> None:
        box = self.query_one("#live-result", Static)
        box.styles.border = ("round", colour)
        box.update(text)
        box.display = True

    def _notify_app(self) -> None:
        refresh = getattr(self.app, "refresh_after_run", None)
        if callable(refresh):
            refresh()

    def _on_tick(self) -> None:
        self.tick += 1
        self.query_one(PipelineStrip).refresh()
        self._refresh_gauges()

    def _refresh_all(self) -> None:
        self.query_one(PipelineStrip).refresh()
        self.query_one(SourceGrid).refresh(layout=True)
        self._refresh_gauges()

    def _refresh_gauges(self) -> None:
        s = self.state
        compact = self.has_class("compact")
        budget = s.budget
        cost = Text()
        cost.append("cost ", style=MUTED)
        cost.append(f"${s.cost:.4f}", style=f"bold {INK}")
        cost.append(f" of ${budget:.2f}" if budget else "", style=MUTED)
        bar = self.query_one("#cost-bar", ProgressBar)
        bar.update(total=budget or 1, progress=min(s.cost, budget or 1))
        bar.set_class(bool(budget) and s.cost >= 0.8 * budget, "hot")
        rate = "—" if s.cache_rate is None else f"{s.cache_rate:.0%}"
        calls = f"llm {s.llm_calls} call{'' if s.llm_calls == 1 else 's'}"
        http = f"http {s.http} req · cache {rate}"
        tokens = f"tokens {s.tokens_in:,} in · {s.tokens_out:,} out"
        elapsed = f"time {fmt_secs(s.elapsed())}"
        rest = Text(style=INK)
        if compact:
            rest.append(f"{tokens}\n{elapsed} · {calls}\n{http}")
        else:
            rest.append(f"{tokens}\n{elapsed}\n{http}\n{calls}")
        self.query_one("#g-cost", Static).update(cost)
        self.query_one("#g-rest", Static).update(rest)


class TraceSources(Message):
    """The sources a radar run is expected to report (shown as pending tiles)."""

    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.names = names
