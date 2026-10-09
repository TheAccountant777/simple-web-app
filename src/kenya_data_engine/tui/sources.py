"""Sources tab: health at a glance, live tests on demand, config shown (never edited)."""

from datetime import UTC, datetime

import yaml
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import DataTable, Input, ProgressBar, Static

from kenya_data_engine.cli.common import State
from kenya_data_engine.cli.sources import SourceRow, SourceTest, source_rows, test_sources
from kenya_data_engine.config import load_sources
from kenya_data_engine.health import failing_label
from kenya_data_engine.home import EngineHome
from kenya_data_engine.tui.runs import short
from kenya_data_engine.tui.widgets import ACCENT, BAD, GOOD, INK, MUTED, WARN, Scrub

MAX_NAME = 22
WIDE = 100  # below this the table abbreviates so that nothing is clipped at 80 columns
KIND_SHORT = {"data_release": "data"}
IDLE = "Select a source: t tests it live, e shows its config, / filters."
EMPTY_SOURCES = "No sources configured — press e to see where sources.yaml lives"


def health_text(row: SourceRow) -> Text:
    if row.health == "ok":
        return Text("● ok", style=GOOD)
    if row.health == "failing":
        return Text(failing_label(row.consecutive_failures), style=BAD)
    if row.health == "invalid":
        return Text("✗ invalid", style=BAD)
    return Text("○ never", style=MUTED)


def when(ts: datetime | None, narrow: bool = False) -> str:
    if ts is None:
        return "—"
    if not narrow:
        return ts.astimezone().strftime("%m-%d %H:%M")
    seconds = max(0, int((datetime.now(UTC) - ts).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "now"


def test_text(res: SourceTest, scrub: Scrub) -> Text:
    t = Text()
    colour = {"ok": GOOD, "empty": WARN, "fail": BAD}[res.status]
    mark = {"ok": "✓ ok", "empty": "! empty", "fail": "✗ fail"}[res.status]
    t.append(mark, style=f"bold {colour}")
    t.append(f"  {res.name}", style=f"bold {INK}")
    t.append(f"  {res.latency_ms} ms · {res.item_count} item{'' if res.item_count == 1 else 's'}\n")
    if res.error:
        t.append(scrub(res.error) + "\n", style=BAD)
        if res.hint:
            t.append(f"→ {scrub(res.hint)}\n", style=MUTED)
    elif res.status == "empty":
        t.append("0 items: the feed is empty or the selectors are stale\n", style=WARN)
    for item in res.items:
        day = item.date.strftime("%m-%d") if item.date else "no date"
        link = (item.url or "").split("://", 1)[-1]
        t.append(f"• {short(scrub(item.title), 38):<38}", style=INK)
        t.append(f"  {day}  {short(link, 24)}\n", style=MUTED)
    return t


class SourcesPane(Widget):
    BINDINGS = [
        Binding("slash", "filter", "Filter"),
        Binding("t", "test_selected", "Test"),
        Binding("T", "test_all", "Test all"),
        Binding("e", "show_config", "Config"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    def __init__(self, home: EngineHome, scrub: Scrub) -> None:
        super().__init__(id="sources-pane")
        self.home = home
        self.scrub = scrub
        self.state = State(home=home)
        self.rows: list[SourceRow] = []
        self.query_text = ""
        self._width = 0
        self._cursor_name: str | None = None  # the row last described, to ignore rebuild events

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter sources…  (enter to apply, esc to clear)", id="filter")
        with Vertical(id="sources-body"):
            yield DataTable(id="sources-table", cursor_type="row")
            yield ProgressBar(total=1, show_eta=False, id="test-progress")
            with VerticalScroll(id="source-detail-box"):
                yield Static(IDLE, id="source-detail")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.border_title = "Sources"
        self.query_one("#source-detail-box").border_title = "Detail"
        self.query_one("#filter").display = False
        self.query_one(ProgressBar).display = False
        self.reload()

    # -- table --
    def reload(self) -> None:
        keep = self.selected()
        self.rows = source_rows(self.state)
        self.render_table()
        if keep is not None:  # a rebuilt table must not move the cursor off the same source
            names = [r.name for r in self.matching_rows()]
            if keep.name in names:
                self.query_one(DataTable).move_cursor(row=names.index(keep.name))

    def matching_rows(self) -> list[SourceRow]:
        q = self.query_text.strip().lower()
        return [r for r in self.rows if q in r.name.lower() or q in (r.kind or "").lower()]

    def render_table(self) -> None:
        table = self.query_one(DataTable)
        width = table.size.width or WIDE
        self._width = width
        narrow = width < WIDE
        name_w = min(
            MAX_NAME if not narrow else 16, max((len(r.name) for r in self.rows), default=4)
        )
        type_w, kind_w, on_w, health_w, ok_w = (4, 9, 2, 11, 7) if narrow else (7, 12, 3, 11, 11)
        used = name_w + type_w + kind_w + on_w + health_w + ok_w + 2 * 7 + 3  # padding + chrome
        err_w = max(8, width - used)
        table.clear(columns=True)
        for label, w in (
            ("Name", name_w),
            ("Type", type_w),
            ("Kind", kind_w),
            ("On", on_w),
            ("Health", health_w),
            ("Last ok", ok_w),
            ("Last error", err_w),
        ):
            table.add_column(label, width=w)
        rows = self.matching_rows()
        for r in rows:
            err = r.error if r.health == "invalid" else r.last_error
            kind = r.kind or "?"
            typ = r.type or "?"
            table.add_row(
                short(r.name, name_w),
                ("list" if typ == "listing" else typ) if narrow else typ,
                short(KIND_SHORT.get(kind, kind) if narrow else kind, kind_w),
                ("✓" if r.enabled else "–") if narrow else ("yes" if r.enabled else "no"),
                health_text(r),
                when(r.last_ok_at, narrow),
                short(self.scrub(err or ""), err_w),
                key=r.name,
            )
        if not rows:
            self.set_detail(
                Text("No source matches the filter." if self.rows else EMPTY_SOURCES, style=MUTED)
            )

    def on_resize(self, event: events.Resize) -> None:
        table = self.query_one(DataTable)
        if table.size.width and table.size.width != self._width:
            row = table.cursor_row
            self.render_table()
            table.move_cursor(row=row)

    def selected(self) -> SourceRow | None:
        rows = self.matching_rows()
        index = self.query_one(DataTable).cursor_row
        return rows[index] if 0 <= index < len(rows) else None

    def set_detail(self, text: Text) -> None:
        self.query_one("#source-detail", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        row = self.selected()
        if row is not None and row.name != self._cursor_name:
            self._cursor_name = row.name
            self.set_detail(self.describe(row))

    def describe(self, row: SourceRow) -> Text:
        t = Text()
        t.append(row.name + "\n", style=f"bold {INK}")
        if row.health == "invalid":
            t.append(self.scrub(row.error or "invalid entry") + "\n", style=BAD)
            return t
        t.append(f"{row.url}\n", style=MUTED)
        t.append("health  ")
        t.append_text(health_text(row))
        if row.last_signal_count is not None:
            t.append(f"  last run found {row.last_signal_count} signals", style=MUTED)
        t.append("\n")
        if row.last_error:
            t.append(self.scrub(row.last_error) + "\n", style=BAD)
        t.append("\nt test live · e config", style=MUTED)
        return t

    # -- filter --
    def action_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.display = True
        box.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_text = event.value
        self.render_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one(DataTable).focus()

    def action_clear_filter(self) -> None:
        box = self.query_one("#filter", Input)
        if box.display or self.query_text:
            box.value = ""
            box.display = False
            self.query_text = ""
            self.render_table()
            self.query_one(DataTable).focus()

    # -- config --
    def action_show_config(self) -> None:
        t = Text()
        t.append("user sources.yaml", style=f"bold {ACCENT}")
        t.append(" · the TUI never edits files\n", style=MUTED)
        exists = self.home.sources_path.exists()
        t.append(f"{self.home.sources_path}{'' if exists else '  (not created yet)'}\n\n")
        row = self.selected()
        spec = load_sources(self.home).specs.get(row.name) if row else None
        if row is not None and spec is not None:
            t.append(f"merged entry for {row.name} — copy into sources.yaml to override\n", MUTED)
            body = yaml.safe_dump({row.name: spec.model_dump(exclude_none=True)}, sort_keys=False)
            t.append(self.scrub(body))
        elif row is not None:
            t.append(f"{row.name} has no valid entry; see the table for the problem\n", style=WARN)
        self.set_detail(t)

    # -- live tests --
    def action_test_selected(self) -> None:
        row = self.selected()
        if row is None or row.health == "invalid":
            self.notify("Pick a valid source to test", timeout=2)
            return
        self.set_detail(Text(f"testing {row.name} live (cache bypassed)…", style=ACCENT))
        self.run_worker(self._test([row.name]), exclusive=True, group="source-test")

    def action_test_all(self) -> None:
        names = [r.name for r in self.rows if r.enabled and r.health != "invalid"]
        if not names:
            self.notify("No enabled sources to test", timeout=2)
            return
        self.run_worker(self._test(names), exclusive=True, group="source-test")

    async def _test(self, names: list[str]) -> None:
        bar = self.query_one(ProgressBar)
        many = len(names) > 1
        bar.display = many
        bar.update(total=len(names), progress=0)
        results: list[SourceTest] = []
        for name in names:
            if many:
                self.set_detail(Text(f"testing {name} ({len(results) + 1}/{len(names)})…"))
            try:
                results.extend(await test_sources(self.state, [name]))
            except Exception as exc:  # one broken probe must not abort the sweep
                results.append(
                    SourceTest(
                        name=name,
                        status="fail",
                        latency_ms=0,
                        item_count=0,
                        items=[],
                        error=str(exc) or type(exc).__name__,
                        hint=getattr(exc, "hint", None),
                    )
                )
            bar.advance(1)
        bar.display = False
        self.reload()
        if many:
            ok = sum(r.status == "ok" for r in results)
            summary = Text(f"{ok}/{len(results)} sources ok\n", style=f"bold {INK}")
            for r in results:
                line = "✓ " if r.status == "ok" else "✗ "
                summary.append(line, style=GOOD if r.status == "ok" else BAD)
                summary.append(f"{r.name:<16}{r.item_count:>4} items  {r.latency_ms} ms")
                if r.error:
                    summary.append(f"  {short(self.scrub(r.error), 40)}", style=MUTED)
                summary.append("\n")
            self.set_detail(summary)
        elif results:
            self.set_detail(test_text(results[0], self.scrub))
