import pytest
from compare_factory import topic, write_topics
from tui_util import screen_text

from kenya_data_engine.tui.app import EngineRoom
from kenya_data_engine.tui.compare import CompareScreen, score_bar5

SIZE = (100, 30)


@pytest.fixture
def home3(tmp_home):
    write_topics(
        tmp_home.runs_dir,
        "2026-10-08-0800",
        topic("z1", "Old story that nobody repeats", ["z9"], score=2.0),
    )
    write_topics(
        tmp_home.runs_dir,
        "2026-10-09-0800",
        topic("a1", "Fuel prices jump", ["s1", "s2"], score=4.2, wallet_impact=5, novelty=3),
        topic("a2", "M-Pesa fee changes", ["s9"], score=3.0),
    )
    write_topics(
        tmp_home.runs_dir,
        "2026-10-09-0900",
        topic("b1", "Petrol cost rise", ["s2", "s1"], score=3.8, wallet_impact=3, novelty=4),
        topic("b2", "Housing levy", ["s20"], score=3.5),
    )
    write_topics(
        tmp_home.runs_dir,
        "2026-10-09-1000",
        topic("c1", "Fuel price shock", ["s1"], score=4.0, wallet_impact=4),
    )
    return tmp_home


async def open_runs(pilot):
    await pilot.press("2")
    await pilot.pause()
    pilot.app.screen.query_one("#run-list").focus()
    await pilot.pause()


def test_score_bar5():
    assert score_bar5(3.5) == "███▌░"
    assert score_bar5(5) == "█████" and score_bar5(0) == "░░░░░" and score_bar5(1.2) == "█░░░░"


async def test_space_marks_runs_and_c_compares_the_selection(home3):
    async with EngineRoom(home3).run_test(size=SIZE) as pilot:
        await open_runs(pilot)
        await pilot.press("space")  # newest run (1000)
        await pilot.press("down", "space")  # 0900
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "2 selected" in text and text.count("✓ 2026-10-09") == 2
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(pilot.app.screen, CompareScreen)
        text = screen_text(pilot.app)
        assert "2 runs" in text and "2/2" in text and "Fuel" in text


async def test_space_again_unmarks(home3):
    async with EngineRoom(home3).run_test(size=SIZE) as pilot:
        await open_runs(pilot)
        await pilot.press("space")
        await pilot.pause()
        assert "1 selected" in screen_text(pilot.app)
        await pilot.press("space")
        await pilot.pause()
        assert "selected" not in screen_text(pilot.app)


async def test_empty_selection_compares_the_last_three_runs(home3):
    async with EngineRoom(home3).run_test(size=SIZE) as pilot:
        await open_runs(pilot)
        await pilot.press("c")
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "3 runs" in text and "3/3" in text
        assert "2026-10-08-0800" not in text and "Old story" not in text


async def test_detail_shows_criteria_ranges_and_appearances(home3):
    async with EngineRoom(home3).run_test(size=SIZE) as pilot:
        await open_runs(pilot)
        await pilot.press("c")
        await pilot.pause()
        text = screen_text(pilot.app)
        # the highlighted (first) group is the fuel story seen in all three runs
        assert "wallet_impact" in text and "[3–5]" in text and "4.0" in text
        assert "novelty" in text and "[3–4]" in text
        for run in ("2026-10-09-0800", "2026-10-09-0900", "2026-10-09-1000"):
            assert run in text
        assert "Petrol cost rise" in text and "Fuel price shock" in text and "#1" in text


async def test_fewer_than_two_runs_shows_a_friendly_message(tmp_home):
    write_topics(tmp_home.runs_dir, "2026-10-09-0800", topic("a", "Fuel", ["s1"]))
    async with EngineRoom(tmp_home).run_test(size=SIZE) as pilot:
        await open_runs(pilot)
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(pilot.app.screen, CompareScreen)
        assert "at least 2 runs" in screen_text(pilot.app)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, CompareScreen)


async def test_enter_opens_the_topic_in_its_run(home3):
    async with EngineRoom(home3).run_test(size=SIZE) as pilot:
        await open_runs(pilot)
        await pilot.press("c")
        await pilot.pause()
        pilot.app.screen.query_one("#members").focus()
        await pilot.pause()
        await pilot.press("enter")  # the first member is the oldest run: 2026-10-09-0800
        await pilot.pause()
        assert not isinstance(pilot.app.screen, CompareScreen)
        text = screen_text(pilot.app)
        assert pilot.app.query_one("TabbedContent").active == "runs"
        assert "SCORE MATHS" in text and "Fuel prices jump" in text


async def test_escape_returns_to_the_runs_tab(home3):
    async with EngineRoom(home3).run_test(size=SIZE) as pilot:
        await open_runs(pilot)
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, CompareScreen)
        assert pilot.app.query_one("TabbedContent").active == "runs"


async def test_compare_fits_80x24(home3):
    async with EngineRoom(home3).run_test(size=(80, 24)) as pilot:
        await open_runs(pilot)
        await pilot.press("c")
        await pilot.pause()
        text = screen_text(pilot.app)
        assert "3/3" in text and "Fuel" in text
        assert max(len(line) for line in text.splitlines()) <= 80
