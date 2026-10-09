"""Runs tab: run list, ranked topics, the score maths and the LLM exchanges behind them."""

import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import DataTable, OptionList, Static
from textual.widgets.option_list import Option

from kenya_data_engine.config import load_config
from kenya_data_engine.home import EngineHome
from kenya_data_engine.models import RadarResult, Signal, Topic, TopicList
from kenya_data_engine.report import RunMetrics, load_run_metrics
from kenya_data_engine.runs import RunHandle, RunStore
from kenya_data_engine.tui.widgets import (
    ACCENT,
    BAD,
    GOOD,
    INK,
    MUTED,
    WARN,
    InspectorScreen,
    Scrub,
    score_bar,
)

CRITERIA = ("data_ability", "wallet_impact", "timeliness", "clarity_gap", "novelty")
SHORT_CATEGORY = {
    "economy": "econ",
    "personal_finance": "fin",
    "startups": "strt",
    "business": "biz",
    "law": "law",
}
MAX_RUNS = 100
EMPTY_RUNS = "No runs yet — press 1 then r to start one"


class Exchange(BaseModel):
    """One captured LLM call (`<run>/llm/NNNN-*.json`); every field tolerates absence."""

    seq: int = 0
    stage: str = ""
    name: str = ""
    topic_id: str | None = None
    model: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)
    messages: list[Any] = Field(default_factory=list)
    output: Any = None
    usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0
    cost_usd: float = 0.0
    status: str = "ok"
    error: str | None = None
    file: str = ""


@dataclass
class RunData:
    run_id: str
    metrics: RunMetrics
    topics: TopicList | None
    signals: dict[str, Signal] = field(default_factory=dict)
    exchanges: list[Exchange] = field(default_factory=list)


def load_exchanges(run_dir: Path) -> list[Exchange]:
    found: list[Exchange] = []
    for path in sorted((run_dir / "llm").glob("[0-9][0-9][0-9][0-9]-*.json")):
        try:
            ex = Exchange.model_validate_json(path.read_text(encoding="utf-8", errors="replace"))
        except ValueError:  # includes pydantic's ValidationError; a torn capture is skipped
            continue
        ex.file = path.name
        found.append(ex)
    return found


def load_run_data(run: RunHandle, budget_usd: float) -> RunData:
    radar = run.read("signals", RadarResult)
    return RunData(
        run_id=run.run_id,
        metrics=load_run_metrics(run, budget_usd),
        topics=run.read("topics", TopicList),
        signals={s.id: s for s in radar.signals} if radar else {},
        exchanges=load_exchanges(run.dir),
    )


def exchanges_for(data: RunData, topic: Topic | None) -> list[Exchange]:
    """The calls behind a topic: its own scoring call plus the run's cluster call."""
    return [
        e
        for e in data.exchanges
        if e.stage == "synthesize_cluster" or (topic is not None and e.topic_id == topic.id)
    ]


def weighted_sum(topic: Topic, weights: dict[str, float]) -> float:
    total: float = sum(getattr(topic.scores, k) * weights.get(k, 0.0) for k in CRITERIA)
    return round(total, 3)


def short(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


# ---- pure text builders (tested directly through the screen) ------------------------------


def run_label(m: RunMetrics) -> Text:
    t = Text(f"{m.run_id} ")
    t.append("✓" if m.complete else "⚠", style=GOOD if m.complete else WARN)
    t.append(f" {m.topics:>2} ", style=INK)
    t.append(f"${m.cost_usd:.3f}", style=MUTED)
    t.no_wrap = True
    t.overflow = "ellipsis"
    return t


def topic_text(topic: Topic, rank: int, weights: dict[str, float] | None, scrub: Scrub) -> Text:
    t = Text()
    t.append(scrub(topic.title) + "\n", style=f"bold {INK}")
    t.append(f"{topic.category} · rank {rank} · final_score ", style=MUTED)
    t.append(f"{topic.final_score:.3f}\n\n", style=f"bold {ACCENT}")
    t.append("SCORE MATHS\n", style=f"bold {ACCENT}")
    t.append(f"{'criterion':<14}{'score':<10}{'weight':<8}points\n", style=MUTED)
    for key in CRITERIA:
        score = getattr(topic.scores, key)
        weight = None if weights is None else weights.get(key, 0.0)
        t.append(f"{key:<13} ")
        t.append(score_bar(score), style=ACCENT)
        t.append(f" {score}")
        if weight is None:
            t.append("\n")
        else:
            t.append(f" × {weight:.2f}  = ", style=MUTED)
            t.append(f"{score * weight:.3f}\n")
    t.append("─" * 37 + "\n", style=MUTED)
    if weights is None:
        t.append("⚠ config weights unavailable; cannot re-add the score\n", style=WARN)
    else:
        total = weighted_sum(topic, weights)
        t.append(f"{'sum':<30}= ")
        t.append(f"{total:.3f}", style=f"bold {INK}")
        if abs(total - round(topic.final_score, 3)) < 0.0005:
            t.append("  ✓\n", style=GOOD)
        else:
            t.append(
                f"\n⚠ weights changed since run: stored final_score is {topic.final_score:.3f}\n",
                style=WARN,
            )
    reasons = [(k, topic.scores.justification.get(k)) for k in CRITERIA]
    if any(why for _, why in reasons):
        t.append("\nJUSTIFICATION\n", style=f"bold {ACCENT}")
        for key, why in reasons:
            if why:
                t.append(f"{key}: ", style=INK)
                t.append(f"{scrub(why)}\n", style=MUTED)
    t.append("\nWHY NOW\n", style=f"bold {ACCENT}")
    t.append(scrub(topic.why_now) + "\n")
    return t


def overview_text(data: RunData) -> Text:
    m = data.metrics
    t = Text()
    t.append(f"{m.run_id}\n", style=f"bold {INK}")
    if m.complete:
        t.append("complete", style=GOOD)
    else:
        t.append("⚠ incomplete", style=WARN)
        t.append(" — no ranked topics were written", style=MUTED)
    t.append(
        f"\n{m.duration_ms / 1000:.1f}s · ${m.cost_usd:.4f} of ${m.budget_usd:.2f}", style=MUTED
    )
    t.append(f"\n{m.http_requests} http · {m.llm.calls} LLM calls · {m.errors} errors\n\n")
    t.append("STAGES\n", style=f"bold {ACCENT}")
    for s in m.stages:
        ok = s.status == "ok"
        t.append("✓ " if ok else "✗ ", style=GOOD if ok else BAD)
        t.append(f"{s.name:<11} {s.duration_ms / 1000:>5.1f}s  ")
        t.append(s.detail + "\n", style=MUTED)
    if not m.stages:
        t.append("no stage finished\n", style=MUTED)
    failed = [s for s in m.sources if s.status == "error"]
    if failed:
        t.append("\nFAILED SOURCES\n", style=f"bold {ACCENT}")
        for src in failed:
            t.append(f"✗ {src.name}: ", style=BAD)
            t.append(short(src.error or "error", 60) + "\n", style=MUTED)
    return t


def signal_option(sig: Signal | None, sid: str, scrub: Scrub) -> Option:
    if sig is None:
        return Option(Text(f"{sid} (not in signals.json)", style=MUTED, no_wrap=True), id=sid)
    when = sig.published_at.date().isoformat() if sig.published_at else "no date"
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(scrub(sig.title))
    t.append(f"  {sig.source} · {when}", style=MUTED)
    return Option(t, id=sid)


def exchange_option(ex: Exchange) -> Option:
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(f"{ex.seq:04d} ", style=MUTED)
    t.append(f"{ex.name:<8}", style=INK)
    t.append(
        f" {ex.usage.get('input_tokens', 0):,}→{ex.usage.get('output_tokens', 0):,} tok"
        f"  ${ex.cost_usd:.4f}  {ex.latency_ms / 1000:.1f}s ",
        style=MUTED,
    )
    t.append("✓" if ex.status == "ok" else "✗", style=GOOD if ex.status == "ok" else BAD)
    return Option(t, id=ex.file)


class RunsPane(Widget):
    """Three panes: runs, topics of the selected run, detail of the selected topic."""

    BINDINGS = [
        Binding("o", "open_url", "Open url"),
        Binding("i", "inspect", "Inspect LLM"),
    ]

    def __init__(self, home: EngineHome, scrub: Scrub) -> None:
        super().__init__(id="runs-pane")
        self.home = home
        self.scrub = scrub
        self.store = RunStore(home.runs_dir)
        self.run_ids: list[str] = []
        self.data: RunData | None = None
        self.weights: dict[str, float] | None = None
        self.budget = 0.0
        self._exchanges: list[Exchange] = []
        self._title_w = 0

    # -- layout --
    def compose(self) -> ComposeResult:
        yield Static(EMPTY_RUNS, id="runs-empty")
        with Horizontal(id="runs-body"):
            with Vertical(id="runs-left"):
                yield OptionList(id="run-list")
                yield DataTable(id="topic-table", cursor_type="row", zebra_stripes=False)
            with VerticalScroll(id="runs-detail"):
                yield Static(id="detail")
                yield OptionList(id="signals")
                yield OptionList(id="exchanges")

    def on_mount(self) -> None:
        self.query_one("#run-list").border_title = "Runs · ✓/⚠ topics cost"
        self.query_one("#topic-table").border_title = "Topics"
        self.query_one("#signals").border_title = "Signals · o opens"
        self.query_one("#exchanges").border_title = "LLM exchanges · enter inspects"
        self.refresh_runs()

    # -- data --
    def refresh_runs(self) -> None:
        try:
            cfg = load_config(self.home)
            self.weights, self.budget = dict(cfg.weights), cfg.budgets.run_usd
        except Exception:  # a broken config must not hide past runs
            self.weights, self.budget = None, 0.0
        self.run_ids = self.store.list()[:MAX_RUNS]
        empty = not self.run_ids
        self.query_one("#runs-empty").display = empty
        self.query_one("#runs-body").display = not empty
        runs = self.query_one("#run-list", OptionList)
        runs.clear_options()
        self.data = None
        for rid in self.run_ids:
            metrics = load_run_metrics(self.store.open(rid), self.budget)
            runs.add_option(Option(run_label(metrics), id=rid))
        if not empty:
            runs.highlighted = 0
            self.show_run(self.run_ids[0])

    def select_run(self, run_id: str) -> None:
        if run_id in self.run_ids:
            self.query_one("#run-list", OptionList).highlighted = self.run_ids.index(run_id)
            self.show_run(run_id)

    def show_run(self, run_id: str) -> None:
        if self.data is not None and self.data.run_id == run_id:
            return
        self.data = load_run_data(self.store.open(run_id), self.budget)
        self.fill_table()
        table = self.query_one(DataTable)
        if self.data.topics and self.data.topics.topics:
            table.move_cursor(row=0)
            self.show_topic(0)
        else:
            self.show_overview()

    def fill_table(self) -> None:
        """(Re)build the topic table; the title column takes whatever width is left."""
        table = self.query_one(DataTable)
        # border 2 + scrollbar 1 + 4 columns x 2 padding, minus the fixed #, Cat and Score cells
        title_w = max(8, table.size.width - 3 - 8 - (2 + 4 + 5)) if table.size.width else 16
        self._title_w = title_w
        table.clear(columns=True)
        table.add_column("#", width=2)
        table.add_column("Title", width=title_w)
        table.add_column("Cat", width=4)
        table.add_column("Score", width=5)
        topics = self.data.topics.topics if self.data and self.data.topics else []
        for rank, tp in enumerate(topics, 1):
            table.add_row(
                str(rank),
                short(self.scrub(tp.title), title_w),
                SHORT_CATEGORY.get(tp.category, tp.category[:4]),
                f"{tp.final_score:.2f}",
                key=tp.id,
            )

    def on_resize(self, event: events.Resize) -> None:
        table = self.query_one(DataTable)
        if (
            self.data is not None
            and table.size.width
            and self._title_w != max(8, table.size.width - 3 - 8 - 11)
        ):
            row = table.cursor_row
            self.fill_table()
            table.move_cursor(row=row)

    def show_overview(self) -> None:
        assert self.data is not None
        self.query_one("#detail", Static).update(overview_text(self.data))
        self._fill_lists(None)

    def show_topic(self, index: int) -> None:
        assert self.data is not None
        if not self.data.topics or index >= len(self.data.topics.topics):
            return
        topic = self.data.topics.topics[index]
        self.query_one("#detail", Static).update(
            topic_text(topic, index + 1, self.weights, self.scrub)
        )
        self._fill_lists(topic)

    def _fill_lists(self, topic: Topic | None) -> None:
        assert self.data is not None
        signals = self.query_one("#signals", OptionList)
        signals.clear_options()
        ids = topic.signal_ids if topic else []
        for sid in ids:
            signals.add_option(signal_option(self.data.signals.get(sid), sid, self.scrub))
        signals.display = bool(ids)
        if ids:
            signals.highlighted = 0
            signals.border_title = f"Signals ({len(ids)}) · o opens"
        exchanges = self.query_one("#exchanges", OptionList)
        exchanges.clear_options()
        self._exchanges = exchanges_for(self.data, topic)
        for ex in self._exchanges:
            exchanges.add_option(exchange_option(ex))
        exchanges.display = bool(self._exchanges)
        if self._exchanges:
            exchanges.highlighted = 0

    # -- events --
    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "run-list" and event.option.id is not None:
            event.stop()
            self.show_run(event.option.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "exchanges":
            event.stop()
            self.action_inspect()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        if self.data is not None and self.data.topics:
            self.show_topic(event.cursor_row)

    # -- actions --
    def action_inspect(self) -> None:
        lst = self.query_one("#exchanges", OptionList)
        if not self._exchanges:
            return
        index = lst.highlighted if lst.highlighted is not None else 0
        self.app.push_screen(InspectorScreen(self._exchanges[index], self.scrub))

    def action_open_url(self) -> None:
        if self.data is None:
            return
        lst = self.query_one("#signals", OptionList)
        if lst.highlighted is None:
            self.notify("Highlight a signal first", timeout=2)
            return
        sig = self.data.signals.get(str(lst.get_option_at_index(lst.highlighted).id))
        if sig is None or not sig.url:
            self.notify("This signal has no URL", timeout=2)
            return
        webbrowser.open(sig.url)
        self.notify(f"Opened {short(sig.url, 50)}", timeout=2)
