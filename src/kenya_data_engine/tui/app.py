"""EngineRoom: the Textual shell with four tabs."""

from collections.abc import Callable
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from kenya_data_engine.home import EngineHome
from kenya_data_engine.pipeline import Stage
from kenya_data_engine.runs import RunStore
from kenya_data_engine.tui.runs import RunsPane
from kenya_data_engine.tui.widgets import HelpScreen, make_scrubber

TABS = ("live", "runs", "sources", "performance")


class EngineRoom(App[None]):
    CSS_PATH = "theme.tcss"
    TITLE = "Kenya Data Engine"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("1", "tab('live')", "Live", show=False),
        Binding("2", "tab('runs')", "Runs", show=False),
        Binding("3", "tab('sources')", "Sources", show=False),
        Binding("4", "tab('performance')", "Perf", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        home: EngineHome,
        *,
        live_stages_factory: Callable[[], list[Stage[Any, Any]]] | None = None,
    ) -> None:
        super().__init__()
        self.home = home
        self.live_stages_factory = live_stages_factory
        self.scrub = make_scrubber(home)

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="runs"):
            with TabPane("1 Live", id="live"):
                # Filled in by the live-view task; the shell only reserves the tab.
                yield Static("Live run view — coming next.", classes="placeholder")
            with TabPane("2 Runs", id="runs"):
                yield RunsPane(self.home, self.scrub)
            with TabPane("3 Sources", id="sources"):
                yield Static("Sources view — coming next.", classes="placeholder")
            with TabPane("4 Performance", id="performance"):
                yield Static("Performance view — coming next.", classes="placeholder")
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_first, "runs")
        count = len(RunStore(self.home.runs_dir).list())
        self.sub_title = f"{self.home.root} · {count} run{'' if count == 1 else 's'}"

    def action_tab(self, name: str) -> None:
        # Blur first: hiding the focused widget would otherwise refocus a sibling in the old
        # pane, and TabbedContent switches back to whichever pane receives focus.
        self.screen.set_focus(None)
        self.query_one(TabbedContent).active = name
        self.call_after_refresh(self._focus_first, name)

    def _focus_first(self, name: str) -> None:
        for child in self.query_one(f"#{name}").query("*"):
            if child.can_focus and child.display:
                child.focus()
                return

    def action_help(self) -> None:
        self.push_screen(HelpScreen())
