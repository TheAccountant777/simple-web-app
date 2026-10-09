"""Compare screen: the consensus of several runs, with the evidence for the highlighted topic."""

from dataclasses import dataclass, field

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, OptionList, Static
from textual.widgets.option_list import Option

from kenya_data_engine.compare import ConsensusTopic, Member, consensus
from kenya_data_engine.models import TopicList
from kenya_data_engine.tui.runs import SHORT_CATEGORY, short
from kenya_data_engine.tui.widgets import ACCENT, GOOD, INK, MUTED, WARN, Scrub

STABILITY_COLOUR = {"strong": GOOD, "mixed": WARN, "noise": MUTED}
NEED_TWO = (
    "Compare needs at least 2 runs with topics.\n\n"
    "Press esc, then 2 for Runs. On Live, R runs the engine several times in a row."
)


def score_bar5(mean: float) -> str:
    """Five cells in half-cell steps: 3.5 -> ███▌░"""
    halves = max(0, min(10, round(mean * 2)))
    full, half = divmod(halves, 2)
    return "█" * full + "▌" * half + "░" * (5 - full - half)


def detail_text(c: ConsensusTopic, scrub: Scrub) -> Text:
    t = Text()
    t.append(scrub(c.title) + "\n", style=f"bold {INK}")
    t.append(f"{c.category} · seen {c.appearances}/{c.runs} · ", style=MUTED)
    t.append(c.stability, style=f"bold {STABILITY_COLOUR[c.stability]}")
    t.append(
        f"\nscore {c.mean_score:.2f} [{c.min_score:.1f}–{c.max_score:.1f}] · "
        f"mean rank {c.mean_rank:.1f}\n\n",
        style=MUTED,
    )
    for name, stat in c.criteria.items():
        t.append(f"{name:<14}", style=MUTED)
        t.append(score_bar5(stat.mean), style=ACCENT)
        t.append(f" {stat.mean:.1f} ", style=INK)
        t.append(f"[{stat.min}–{stat.max}]\n", style=MUTED)
    return t


def member_option(m: Member, scrub: Scrub) -> Option:
    t = Text()
    t.append(f"{m.run_id} ", style=INK)
    t.append(f"#{m.rank} ", style=ACCENT)
    t.append(f"{m.score:.2f}\n", style=INK)
    t.append("  " + short(scrub(m.title), 32), style=MUTED)
    return Option(t, id=f"{m.run_id}|{m.topic_id}")


@dataclass
class CompareData:
    topics: list[ConsensusTopic]
    run_ids: list[str]
    skipped: list[str] = field(default_factory=list)


def build_compare(runs: list[tuple[str, TopicList]], skipped: list[str]) -> CompareData:
    return CompareData(consensus(runs) if len(runs) >= 2 else [], [r for r, _ in runs], skipped)


class CompareScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "open", "Open in run"),
    ]

    def __init__(self, data: CompareData, scrub: Scrub) -> None:
        super().__init__(id="compare")
        self.data = data
        self.scrub = scrub
        self._members: list[Member] = []

    def compose(self) -> ComposeResult:
        yield Static(id="compare-head")
        yield Static(NEED_TWO, id="compare-empty")
        yield DataTable(id="consensus", cursor_type="row", zebra_stripes=False)
        with Horizontal(id="compare-body"):
            with VerticalScroll(id="compare-detail-box"):
                yield Static(id="compare-detail")
            yield OptionList(id="members")
        yield Footer()

    def on_mount(self) -> None:
        d = self.data
        enough = len(d.run_ids) >= 2
        head = Text()
        head.append("Compare ", style=f"bold {INK}")
        head.append(f"{len(d.run_ids)} runs · " + ", ".join(d.run_ids), style=MUTED)
        if d.skipped:
            head.append(f" · skipped (no topics): {', '.join(d.skipped)}", style=WARN)
        self.query_one("#compare-head", Static).update(head)
        self.query_one("#compare-empty").display = not enough
        self.query_one("#consensus").display = enough
        self.query_one("#compare-body").display = enough
        self.query_one("#consensus").border_title = "Consensus · seen k/N, mean score"
        self.query_one("#compare-detail-box").border_title = "Evidence"
        self.query_one("#members").border_title = "In each run · enter opens"
        if enough:
            self.call_after_refresh(self.fill_table)
            self.query_one("#consensus").focus()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.height < 28, "compact")
        if len(self.data.run_ids) >= 2 and self.is_mounted:
            self.fill_table()

    def fill_table(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        fixed = 2 + 4 + 4 + 5 + 9 + 4 + 6
        title_w = max(10, (table.size.width or 80) - 3 - fixed - 2 * 8)
        table.clear(columns=True)
        for name, width in (
            ("#", 2),
            ("Topic", title_w),
            ("Cat", 4),
            ("Seen", 4),
            ("Mean", 5),
            ("Range", 9),
            ("Rank", 4),
            ("Stab", 6),
        ):
            table.add_column(name, width=width)
        for n, c in enumerate(self.data.topics, 1):
            table.add_row(
                str(n),
                short(self.scrub(c.title), title_w),
                SHORT_CATEGORY.get(c.category, c.category[:4]),
                f"{c.appearances}/{c.runs}",
                f"{c.mean_score:.2f}",
                f"{c.min_score:.1f}–{c.max_score:.1f}",
                f"{c.mean_rank:.1f}",
                Text(c.stability, style=STABILITY_COLOUR[c.stability]),
                key=c.key,
            )
        if self.data.topics:
            table.move_cursor(row=min(row, len(self.data.topics) - 1))
            self.show_topic(table.cursor_row)

    def show_topic(self, index: int) -> None:
        if not 0 <= index < len(self.data.topics):
            return
        c = self.data.topics[index]
        self._members = c.members
        self.query_one("#compare-detail", Static).update(detail_text(c, self.scrub))
        members = self.query_one("#members", OptionList)
        members.clear_options()
        members.add_options([member_option(m, self.scrub) for m in c.members])
        members.highlighted = 0

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self.show_topic(event.cursor_row)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_open()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is not None:
            self._open_member(str(event.option.id))

    def action_open(self) -> None:
        members = self.query_one("#members", OptionList)
        if not self._members:
            return
        index = members.highlighted if members.highlighted is not None else len(self._members) - 1
        m = self._members[index]
        self._open_member(f"{m.run_id}|{m.topic_id}")

    def _open_member(self, key: str) -> None:
        run_id, _, topic_id = key.partition("|")
        self.app.pop_screen()
        open_run = getattr(self.app, "open_run", None)
        if callable(open_run):
            open_run(run_id, topic_id)

    def action_back(self) -> None:
        self.app.pop_screen()
