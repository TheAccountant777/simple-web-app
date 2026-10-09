import asyncio
from datetime import UTC, datetime

import pytest
from tui_util import screen_text

from kenya_data_engine.errors import BudgetExceeded
from kenya_data_engine.models import RadarResult, TopicList
from kenya_data_engine.runs import RunStore
from kenya_data_engine.trace import TraceEvent
from kenya_data_engine.tui.app import EngineRoom
from kenya_data_engine.tui.live import (
    LivePane,
    LiveState,
    format_event_line,
)

SIZE = (100, 30)


class Gates:
    """Lets a test hold a fake stage mid-run and release it on demand."""

    def __init__(self) -> None:
        self.radar_started = asyncio.Event()
        self.radar_go = asyncio.Event()
        self.synth_started = asyncio.Event()
        self.synth_go = asyncio.Event()


def event(kind, name, *, stage="radar", status="ok", latency=120, **kw):
    return TraceEvent(
        ts=datetime.now(UTC),
        run_id="r",
        stage=stage,
        kind=kind,
        name=name,
        status=status,
        latency_ms=latency,
        **kw,
    )


def fake_stages(gates: Gates | None = None, *, fail: Exception | None = None, secret: str = ""):
    class FakeRadar:
        name = "radar"
        output_name = "signals"
        output_type = RadarResult

        def describe(self, out):
            return "12 signals · 1 sources failed"

        async def run(self, ctx, inp):
            if gates:
                gates.radar_started.set()
                await gates.radar_go.wait()
            async with ctx.tracer.span("radar", "tool", "nation") as attrs:
                attrs["signals"] = 12
                attrs["http"] = 2
                attrs["cache_hits"] = 0
            ctx.tracer.record(
                event("http", "nation.africa", stage="http", attrs={"from_cache": True})
            )
            ctx.tracer.record(
                event("http", "standard.co.ke", stage="http", attrs={"from_cache": False})
            )
            try:
                async with ctx.tracer.span("radar", "tool", "standard") as attrs:
                    attrs["http"] = 1
                    attrs["cache_hits"] = 0
                    raise RuntimeError(f"HTTP 503 {secret}".strip())
            except RuntimeError:
                pass
            return RadarResult(signals=[], errors=[], collected_at=datetime.now(UTC))

    class FakeSynth:
        name = "synthesize"
        output_name = "topics"
        output_type = TopicList

        def describe(self, out):
            return "4 topics · 1 dropped"

        async def run(self, ctx, inp):
            ctx.tracer.record_llm("synthesize_cluster", "cluster", 1200, 300, 900)
            if gates:
                gates.synth_started.set()
                await gates.synth_go.wait()
            if fail is not None:
                raise fail
            return TopicList(topics=[], dropped=["x"])

    return [FakeRadar(), FakeSynth()]


async def wait_for(pilot, predicate, *, timeout=5.0):
    loops = int(timeout / 0.05)
    for _ in range(loops):
        await pilot.pause(0.05)
        if predicate():
            return
    raise AssertionError(f"condition not reached; screen was:\n{screen_text(pilot.app)}")


def app_with(home, stages_factory):
    return EngineRoom(home, live_stages_factory=stages_factory)


async def start_run(pilot):
    await pilot.press("1")
    await pilot.pause()
    await pilot.press("r")


# ---- pure pieces --------------------------------------------------------------------------


def test_live_state_accumulates_gauges():
    s = LiveState(budget=0.5)
    s.apply_event(event("llm", "cluster", input_tokens=1000, output_tokens=200, cost_usd=0.02))
    s.apply_event(event("http", "a.ke", attrs={"from_cache": True}))
    s.apply_event(event("http", "b.ke", attrs={"from_cache": False}))
    assert (s.tokens_in, s.tokens_out, s.llm_calls) == (1000, 200, 1)
    assert s.cost == pytest.approx(0.02) and (s.http, s.cache_hits) == (2, 1)
    assert s.cache_rate == 0.5
    assert LiveState(budget=0.5).cache_rate is None


def test_event_line_formats_each_kind_and_redacts():
    scrub = lambda t: t.replace("sk-secret", "***")  # noqa: E731
    llm = event("llm", "cluster", input_tokens=1200, output_tokens=300, cost_usd=0.0012)
    assert "1,200→300 tok" in format_event_line(llm, scrub, 100).plain
    assert "$0.0012" in format_event_line(llm, scrub, 100).plain
    hit = event("http", "a.ke", attrs={"from_cache": True})
    assert "cache hit" in format_event_line(hit, scrub, 100).plain
    miss = event("http", "a.ke", attrs={"from_cache": False})
    assert "cache miss" in format_event_line(miss, scrub, 100).plain
    bad = event("tool", "nation", status="error", error="boom sk-secret")
    plain = format_event_line(bad, scrub, 100).plain
    assert "***" in plain and "sk-secret" not in plain
    assert (
        len(format_event_line(bad.model_copy(update={"error": "x" * 500}), scrub, 80).plain) <= 80
    )


# ---- the pilot tests the brief asks for ----------------------------------------------------


async def test_live_run_updates_pipeline_and_gauges(tmp_home):
    gates = Gates()
    app = app_with(tmp_home, lambda: fake_stages(gates))
    async with app.run_test(size=SIZE) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: gates.radar_started.is_set())
        text = screen_text(app)
        assert "radar" in text and "synthesize" in text and "idle" in text
        assert any(ch in text for ch in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")  # radar is running
        gates.radar_go.set()
        await wait_for(pilot, lambda: gates.synth_started.is_set())
        await wait_for(pilot, lambda: "12 signals · 1 sources failed" in screen_text(app))
        text = screen_text(app)
        assert "✓" in text and "tokens" in text
        await wait_for(pilot, lambda: "1,200" in screen_text(app))  # the llm event arrived
        text = screen_text(app)
        assert "1,200 in" in text and "300 out" in text and "$0.0" in text
        assert "http 2 req" in text and "cache 50%" in text and "llm 1 call" in text
        gates.synth_go.set()
        await wait_for(pilot, lambda: "complete" in screen_text(app))
        text = screen_text(app)
        assert "4 topics · 1 dropped" in text and "enter" in text
        assert not app.query_one(LivePane).running


async def test_source_tiles_from_radar_spans(tmp_home):
    gates = Gates()
    app = app_with(tmp_home, lambda: fake_stages(gates))
    async with app.run_test(size=SIZE) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: gates.radar_started.is_set())
        pending = screen_text(app)
        assert "○ nation" in pending  # configured source, nothing heard yet
        gates.radar_go.set()
        await wait_for(pilot, lambda: "✓ nation" in screen_text(app))
        text = screen_text(app)
        assert "✗ standard" in text
        assert "12" in text.split("✓ nation")[1].splitlines()[0]  # signals on the tile
        gates.synth_go.set()


async def test_event_stream_lines_redacted(tmp_home, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-supersecret-9999")
    app = app_with(tmp_home, lambda: fake_stages(secret="key=sk-supersecret-9999"))
    async with app.run_test(size=SIZE) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: "complete" in screen_text(app))
        text = screen_text(app)
        assert "sk-supersecret-9999" not in text
        assert "***" in text and "cache hit" in text and "cache miss" in text
        assert "tool" in text and "llm" in text and "http" in text
        assert "HTTP 503" in text


async def test_live_failure_shows_hint_and_app_survives(tmp_home):
    err = BudgetExceeded(
        "run budget of $0.50 exhausted", hint="raise budgets.run_usd in config.yaml"
    )
    failing = {"on": True}
    app = app_with(tmp_home, lambda: fake_stages(fail=err if failing["on"] else None))
    async with app.run_test(size=SIZE) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: "run budget of $0.50 exhausted" in screen_text(app))
        text = screen_text(app)
        assert "raise budgets.run_usd in config.yaml" in text
        assert "✗" in text and not app.query_one(LivePane).running
        assert app.query_one(LivePane).state.stages["synthesize"].state == "error"
        # the app is still usable: other tabs open, and a new run can start and succeed
        failing["on"] = False
        await pilot.press("3")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("r")
        await wait_for(pilot, lambda: "complete" in screen_text(app))
        assert len(RunStore(tmp_home.runs_dir).list()) == 2


async def test_unexpected_error_is_shown_not_raised(tmp_home):
    app = app_with(tmp_home, lambda: fake_stages(fail=RuntimeError("provider exploded")))
    async with app.run_test(size=SIZE) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: "provider exploded" in screen_text(app))
        assert app.is_running and not app.query_one(LivePane).running


async def test_missing_key_without_injected_stages_shows_hint(tmp_home):
    async with EngineRoom(tmp_home).run_test(size=SIZE) as pilot:  # real pipeline, no key
        await start_run(pilot)
        await wait_for(pilot, lambda: "DEEPSEEK_API_KEY not set" in screen_text(pilot.app))
        assert "engine init" in screen_text(pilot.app)


async def test_cancel_run(tmp_home):
    gates = Gates()
    store = RunStore(tmp_home.runs_dir)
    app = app_with(tmp_home, lambda: fake_stages(gates))
    async with app.run_test(size=SIZE) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: gates.radar_started.is_set())
        await pilot.press("r")  # only one run at a time
        await pilot.pause()
        assert len(store.list()) == 1
        await pilot.press("x")
        await wait_for(pilot, lambda: "cancelled" in screen_text(app))
        assert not app.query_one(LivePane).running
        run_dir = tmp_home.runs_dir / RunStore(tmp_home.runs_dir).list()[0]
        assert (run_dir / "summary.json").exists()  # the pipeline still wrote its summary
        await pilot.press("r")  # and a new run may start
        await wait_for(pilot, lambda: len(store.list()) == 2)
        gates.radar_go.set()
        gates.synth_go.set()


async def test_resize_during_run(tmp_home):
    gates = Gates()
    app = app_with(tmp_home, lambda: fake_stages(gates))
    async with app.run_test(size=SIZE) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: gates.radar_started.is_set())
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        small = screen_text(app)
        assert "radar" in small and "synthesize" in small and "tokens" in small
        assert max(len(line) for line in small.splitlines()) <= 80
        gates.radar_go.set()
        await wait_for(pilot, lambda: "✓ nation" in screen_text(app))  # the stream continues
        await pilot.resize_terminal(100, 30)
        await pilot.pause()
        assert "cache hit" in screen_text(app)
        gates.synth_go.set()
        await wait_for(pilot, lambda: "complete" in screen_text(app))


async def test_layout_at_80x24_keeps_everything_visible(tmp_home):
    app = app_with(tmp_home, lambda: fake_stages())
    async with app.run_test(size=(80, 24)) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: "complete" in screen_text(app))
        text = screen_text(app)
        for needle in ("radar", "synthesize", "Sources", "cost", "tokens", "cache", "enter"):
            assert needle in text, needle


async def test_enter_opens_finished_run_in_runs_tab(tmp_home):
    app = app_with(tmp_home, lambda: fake_stages())
    async with app.run_test(size=SIZE) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: "complete" in screen_text(app))
        run_id = RunStore(tmp_home.runs_dir).list()[0]
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("TabbedContent").active == "runs"
        assert run_id in screen_text(app)


async def test_enter_before_any_run_is_harmless(tmp_home):
    app = app_with(tmp_home, lambda: fake_stages())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("TabbedContent").active == "live"
        assert "press r" in screen_text(app).lower()


async def test_event_stream_is_capped_at_2000_lines(tmp_home):
    app = app_with(tmp_home, lambda: fake_stages())
    async with app.run_test(size=SIZE) as pilot:
        pane = app.query_one(LivePane)
        for i in range(2100):
            pane.write_event(event("http", f"h{i}.ke", attrs={"from_cache": False}))
        await pilot.pause()
        assert len(pane.query_one("#events").lines) <= 2000


async def test_scrolling_up_pauses_autoscroll(tmp_home):
    app = app_with(tmp_home, lambda: fake_stages())
    async with app.run_test(size=SIZE) as pilot:
        pane = app.query_one(LivePane)
        log = pane.query_one("#events")
        for i in range(80):
            pane.write_event(event("http", f"h{i}.ke", attrs={"from_cache": False}))
        await pilot.pause()
        assert log.is_vertical_scroll_end
        log.scroll_to(y=0, animate=False)
        await pilot.pause()
        for i in range(10):
            pane.write_event(event("http", f"late{i}.ke", attrs={"from_cache": False}))
        await pilot.pause()
        assert log.scroll_y == 0  # the reader stays where they scrolled


async def test_empty_home_runs_hint_is_accurate(tmp_home):
    """Extras E4: the Runs empty state points at 1 then r, and that really starts a run."""
    app = app_with(tmp_home, lambda: fake_stages())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert "press 1 then r" in screen_text(app)
        await start_run(pilot)
        await wait_for(pilot, lambda: "complete" in screen_text(app))
        assert RunStore(tmp_home.runs_dir).list()


# ---- live controls: buttons, run again, run xN ----------------------------------------------


async def test_new_run_button_starts_a_run_and_becomes_run_again(tmp_home):
    from textual.widgets import Button

    gates = Gates()
    app = app_with(tmp_home, lambda: fake_stages(gates))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("1")
        await pilot.pause()
        button = app.query_one("#btn-run", Button)
        assert "New run" in str(button.label) and not button.disabled
        assert "▶ New run" in screen_text(app) and "Run ×N" in screen_text(app)
        await pilot.click("#btn-run")
        await wait_for(pilot, lambda: gates.radar_started.is_set())
        assert button.disabled and app.query_one("#btn-multi", Button).disabled
        gates.radar_go.set()
        gates.synth_go.set()
        await wait_for(pilot, lambda: "complete" in screen_text(app))
        assert not button.disabled and "↻ Run again" in str(button.label)


async def test_button_says_run_again_after_a_failure(tmp_home):
    from textual.widgets import Button

    app = app_with(tmp_home, lambda: fake_stages(fail=RuntimeError("nope")))
    async with app.run_test(size=SIZE) as pilot:
        await start_run(pilot)
        await wait_for(pilot, lambda: "nope" in screen_text(app))
        assert "↻ Run again" in str(app.query_one("#btn-run", Button).label)


async def test_run_n_times_sequentially(tmp_home):
    gates = Gates()
    app = app_with(tmp_home, lambda: fake_stages(gates))
    store = RunStore(tmp_home.runs_dir)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("R")
        await pilot.pause()
        assert "How many runs" in screen_text(app)
        await pilot.press("2")
        await pilot.press("enter")
        await wait_for(pilot, lambda: gates.radar_started.is_set())
        assert "run 1/2" in screen_text(app)
        gates.radar_go.set()
        gates.synth_go.set()
        await wait_for(pilot, lambda: "2 runs complete" in screen_text(app))
        assert len(store.list()) == 2
        assert len(app.query_one("RunsPane").run_ids) == 2  # each run showed up in the Runs tab


async def test_run_n_default_is_three_and_escape_cancels(tmp_home):
    app = app_with(tmp_home, lambda: fake_stages())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("R")
        await pilot.pause()
        assert "3" in screen_text(app)
        await pilot.press("escape")
        await pilot.pause()
        assert not app.query_one(LivePane).running
        await pilot.press("R")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: "3 runs complete" in screen_text(app))
        assert len(RunStore(tmp_home.runs_dir).list()) == 3


async def test_cancel_stops_the_remaining_runs(tmp_home):
    gates = Gates()
    app = app_with(tmp_home, lambda: fake_stages(gates))
    store = RunStore(tmp_home.runs_dir)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("R")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: gates.radar_started.is_set())
        await pilot.press("x")
        await wait_for(pilot, lambda: "cancelled" in screen_text(app))
        await pilot.pause(0.3)
        assert len(store.list()) == 1  # runs 2 and 3 never started


async def test_failure_stops_the_remaining_runs(tmp_home):
    app = app_with(tmp_home, lambda: fake_stages(fail=RuntimeError("provider down")))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("R")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for(pilot, lambda: "provider down" in screen_text(app))
        await pilot.pause(0.3)
        assert len(RunStore(tmp_home.runs_dir).list()) == 1
