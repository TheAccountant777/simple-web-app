from datetime import UTC, datetime

import pytest
from tui_util import screen_text

from kenya_data_engine.cli.sources import SourceTest
from kenya_data_engine.cli.sources import TestedItem as Item
from kenya_data_engine.health import HealthStore
from kenya_data_engine.tui.app import EngineRoom

SIZE = (100, 30)


@pytest.fixture
def health_home(tmp_home):
    store = HealthStore(tmp_home.db_path)
    store.record_ok("nation", 12)
    store.record_failure("standard", "HTTP 503 from cbk.go.ke")
    store.record_failure("standard", "HTTP 503 from cbk.go.ke")
    return tmp_home


def fake_tests(results):
    async def fake(state, names):
        return [results[n] for n in names]

    return fake


def ok_result(name="nation"):
    return SourceTest(
        name=name,
        status="ok",
        latency_ms=321,
        item_count=7,
        items=[
            Item(
                title=f"Story number {i}",
                date=datetime(2026, 10, 9, tzinfo=UTC),
                url=f"https://n.ke/{i}",
            )
            for i in range(5)
        ],
        error=None,
        hint=None,
    )


async def open_sources(home, monkeypatch, results=None):
    if results is not None:
        monkeypatch.setattr("kenya_data_engine.tui.sources.test_sources", fake_tests(results))
    app = EngineRoom(home)
    return app


async def test_sources_table_shows_health(health_home):
    async with EngineRoom(health_home).run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        text = screen_text(pilot.app)
        for col in ("Name", "Type", "Kind", "On", "Health", "Last ok", "Last error"):
            assert col in text
        assert "nation" in text and "● ok" in text
        assert "failing ×2" in text and "HTTP 503" in text
        assert "○ never" in text


async def test_test_selected_source_shows_items(health_home, monkeypatch):
    monkeypatch.setattr(
        "kenya_data_engine.tui.sources.test_sources",
        fake_tests({"standard": ok_result("standard")}),
    )
    async with EngineRoom(health_home).run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table = pilot.app.screen.query_one("#sources-table")
        table.move_cursor(
            row=[r.name for r in pilot.app.screen.query_one("SourcesPane").rows].index("standard")
        )
        await pilot.press("t")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "✓ ok" in text and "321 ms" in text and "7 items" in text
        assert "Story number 0" in text and "Story number 4" in text


async def test_failing_source_shows_hint(health_home, monkeypatch):
    bad = SourceTest(
        name="nation",
        status="fail",
        latency_ms=80,
        item_count=0,
        items=[],
        error="HTTP 503 Service Unavailable",
        hint="the site is down; retry later",
    )
    monkeypatch.setattr("kenya_data_engine.tui.sources.test_sources", fake_tests({"nation": bad}))
    async with EngineRoom(health_home).run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        pane = pilot.app.screen.query_one("SourcesPane")
        pilot.app.screen.query_one("#sources-table").move_cursor(
            row=[r.name for r in pane.rows].index("nation")
        )
        await pilot.press("t")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "✗ fail" in text and "HTTP 503 Service Unavailable" in text
        assert "→ the site is down; retry later" in text


async def test_filter(health_home):
    async with EngineRoom(health_home).run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        await pilot.press("slash")
        await pilot.press(*"cbk")
        await pilot.pause()
        pane = pilot.app.screen.query_one("SourcesPane")
        names = [r.name for r in pane.matching_rows()]
        assert names and all("cbk" in n for n in names) and "nation" not in names
        assert "nation" not in screen_text(pilot.app).split("Sources ─")[1].split("Detail")[0]
        await pilot.press("enter")  # keeps the filter, returns to the table
        await pilot.press("escape")
        await pilot.pause()
        assert len(pane.matching_rows()) == len(pane.rows) > 1
        await pilot.press("slash")
        await pilot.press(*"zzz")
        await pilot.pause()
        assert "No source matches the filter." in screen_text(pilot.app)


async def test_test_all_runs_every_enabled_source_with_summary(health_home, monkeypatch):
    async def fake(state, names):
        (name,) = names
        if name == "nation":
            raise RuntimeError("probe blew up")
        return [ok_result(name)]

    monkeypatch.setattr("kenya_data_engine.tui.sources.test_sources", fake)
    async with EngineRoom(health_home).run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        pane = pilot.app.screen.query_one("SourcesPane")
        enabled = [r.name for r in pane.rows if r.enabled]
        await pilot.press("T")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        text = screen_text(pilot.app)
        assert f"{len(enabled) - 1}/{len(enabled)} sources ok" in text
        assert "probe blew up" in text


async def test_show_config_and_empty_states(health_home):
    health_home.sources_path.write_text("broken_one:\n  type: nope\n")
    async with EngineRoom(health_home).run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "sources.yaml" in text and "never edits files" in text
        assert "merged entry for" in text and "url:" in text
        pane = pilot.app.screen.query_one("SourcesPane")
        invalid = [r.name for r in pane.rows].index("broken_one")
        pilot.app.screen.query_one("#sources-table").move_cursor(row=invalid)
        await pilot.pause()
        assert "invalid" in screen_text(pilot.app)
        await pilot.press("t")  # refuses to test an invalid entry
        await pilot.press("e")
        await pilot.pause()
        assert "no valid entry" in screen_text(pilot.app)


async def test_fits_80x24(health_home):
    async with EngineRoom(health_home).run_test(size=(80, 24)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "Health" in text and "failing ×2" in text and "Test" in text and "Quit" in text
