import json

import pytest
from runs_factory import TOPICS, make_run
from tui_util import add_exchange, add_signals, screen_text
from typer.testing import CliRunner

from kenya_data_engine.cli.app import app as cli_app
from kenya_data_engine.tui.app import EngineRoom
from kenya_data_engine.tui.widgets import InspectorScreen

SIZE = (100, 30)


@pytest.fixture
def home_with_runs(tmp_home):
    old = make_run(tmp_home.runs_dir, "2026-10-08-0800")
    new = make_run(tmp_home.runs_dir, "2026-10-09-0900")
    add_signals(new)
    add_exchange(new, 1, name="cluster", topic_id=None, stage="synthesize_cluster")
    add_exchange(new, 2)
    return tmp_home, old, new


async def test_runs_tab_lists_runs_newest_first(home_with_runs):
    home, _, _ = home_with_runs
    async with EngineRoom(home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        text = screen_text(pilot.app)
        assert text.index("2026-10-09-0900") < text.index("2026-10-08-0800")
        assert "2 runs" in pilot.app.sub_title and str(home.root) in pilot.app.sub_title
        assert "✓" in text and "$0.050" in text and "Fuel prices" in text


async def test_topic_detail_shows_score_maths_matching_final(home_with_runs):
    home, _, _ = home_with_runs
    async with EngineRoom(home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "SCORE MATHS" in text
        assert "data_ability" in text and "× 0.35  = 1.400" in text
        assert "× 0.10  = 0.400" in text
        assert "sum" in text and "= 4.000  ✓" in text and "final_score 4.000" in text
        assert "⚠" not in text.split("SCORE MATHS")[1]
        assert "Fuel pump prices rise again" in text  # signals listed


async def test_weights_mismatch_warning(home_with_runs):
    home, _, _ = home_with_runs
    home.config_path.write_text(
        "weights: {data_ability: 0.5, wallet_impact: 0.1, timeliness: 0.1,"
        " clarity_gap: 0.1, novelty: 0.2}\n"
    )
    # 4 * (0.5 + 0.1 + 0.1 + 0.1 + 0.2) is still 4.0, so make the stored score differ
    new = home.runs_dir / "2026-10-09-0900" / "topics.json"
    data = json.loads(new.read_text())
    data["topics"][0]["scores"]["data_ability"] = 5
    new.write_text(json.dumps(data))
    async with EngineRoom(home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "weights changed since run" in text and "⚠" in text
        assert "4.500" in text  # what the current weights give


async def test_llm_inspector_shows_prompt_and_output(home_with_runs):
    home, _, _ = home_with_runs
    async with EngineRoom(home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        pilot.app.screen.query_one("#exchanges").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(pilot.app.screen, InspectorScreen)
        text = screen_text(pilot.app)
        assert "deepseek-chat" in text and "1,200 in" in text and "$0.0012" in text
        assert "INSTRUCTIONS" in text and "careful editor" in text
        assert "USER" in text and "Score the fuel topic" in text
        assert "tool call final_result" in text and "PARSED OUTPUT" in text
        assert '"data_ability": 4' in text
        await pilot.press("c")  # copy must not raise
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, InspectorScreen)


async def test_inspector_masks_secrets(home_with_runs, monkeypatch):
    home, _, new = home_with_runs
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-supersecret")
    add_exchange(
        new,
        3,
        name="x",
        messages=[{"parts": [{"part_kind": "user-prompt", "content": "k=sk-supersecret"}]}],
    )
    async with EngineRoom(home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        pilot.app.screen.query_one("#exchanges").focus()
        await pilot.press("i")
        await pilot.pause()
        assert "sk-supersecret" not in screen_text(pilot.app)


async def test_incomplete_run_renders(tmp_home):
    run = make_run(tmp_home.runs_dir, "2026-10-09-0900", complete=False, kenya_ok=False)
    (run.dir / "llm").mkdir()
    (run.dir / "llm" / "0001-synthesize_cluster-cluster.json").write_text("{torn")
    async with EngineRoom(tmp_home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "⚠" in text and "incomplete" in text and "kenya_news" in text
        await pilot.press("down")  # nothing to move to, must not raise


async def test_empty_home_message(tmp_home):
    async with EngineRoom(tmp_home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert "No runs yet — press 1 then r to start one" in screen_text(pilot.app)
        await pilot.press("3", "2", "4", "1")


async def test_long_prompt_scrolls_without_error(home_with_runs):
    home, _, new = home_with_runs
    long = "word " * 20000
    add_exchange(
        new,
        3,
        name="big",
        messages=[{"parts": [{"part_kind": "user-prompt", "content": long}]}],
    )
    async with EngineRoom(home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        pilot.app.screen.query_one("#exchanges").focus()
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        await pilot.press("pagedown", "home")
        await pilot.press("end")
        await pilot.pause()
        assert "more characters" in screen_text(pilot.app)


async def test_many_signals_and_long_titles(home_with_runs):
    home, _, new = home_with_runs
    data = json.loads((new.dir / "topics.json").read_text())
    ids = [f"s{i}" for i in range(300)]
    data["topics"][0]["signal_ids"] = ids
    data["topics"][0]["title"] = "A very long topic title " * 20
    (new.dir / "topics.json").write_text(json.dumps(data))
    add_signals(new, ids)
    async with EngineRoom(home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        signals = pilot.app.screen.query_one("#signals")
        assert signals.option_count == 300 and "Signals (300)" in signals.border_title


async def test_open_url_and_keys(home_with_runs, monkeypatch):
    home, _, _ = home_with_runs
    opened = []
    monkeypatch.setattr("kenya_data_engine.tui.runs.webbrowser.open", opened.append)
    async with EngineRoom(home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("o")  # the first signal is pre-highlighted
        assert opened == ["https://example.ke/story"]
        pilot.app.screen.query_one("#signals").clear_options()
        await pilot.press("o")  # nothing to open: a hint, not an error
        assert len(opened) == 1
        await pilot.press("question_mark")
        await pilot.pause()
        assert "Tabs" in screen_text(pilot.app)
        await pilot.press("escape")
        await pilot.press("1", "4")
        await pilot.pause()
        assert "coming next" in screen_text(pilot.app)
        await pilot.press("2")
        await pilot.press("q")


async def test_switching_runs(home_with_runs):
    home, _, _ = home_with_runs
    async with EngineRoom(home).run_test(size=SIZE) as pilot:
        await pilot.pause()
        pilot.app.screen.query_one("#run-list").focus()
        await pilot.press("down")
        await pilot.pause()
        assert "2026-10-08-0800" in screen_text(pilot.app)
        assert "LLM exchanges" not in screen_text(pilot.app).split("SCORE MATHS")[1]


async def test_fits_80x24(home_with_runs):
    home, _, _ = home_with_runs
    async with EngineRoom(home).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "SCORE MATHS" in text and "2026-10-09-0900" in text and "Quit" in text
        assert "× 0.35  = 1.400" in text


def test_cli_tui_and_browse_alias(tmp_home, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "kenya_data_engine.cli.tui.EngineRoom.run", lambda self: calls.append(self.home)
    )
    runner = CliRunner()
    for cmd in ("tui", "browse"):
        r = runner.invoke(cli_app, ["--home", str(tmp_home.root), cmd])
        assert r.exit_code == 0, r.output
    assert len(calls) == 2


def test_topics_fixture_sanity():
    assert TOPICS["topics"][0]["final_score"] == 4.0
