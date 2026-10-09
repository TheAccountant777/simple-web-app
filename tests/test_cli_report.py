import json

from runs_factory import make_run
from typer.testing import CliRunner

from kenya_data_engine.cli.app import app

runner = CliRunner()


def invoke(home, *args):
    return runner.invoke(app, ["--home", str(home.root), "report", *args])


def test_report_empty_home(tmp_home):
    r = invoke(tmp_home)
    assert r.exit_code == 0 and "No runs yet" in r.stdout and "engine run" in r.stdout


def test_report_aggregate_and_single(tmp_home):
    tmp_home.sources_path.write_text(
        "kenya_news:\n  type: rss\n  url: https://k.ke/feed\n  kind: news\n"
        "cbk:\n  type: rss\n  url: https://c.ke/feed\n  kind: news\n",
        encoding="utf-8",
    )
    make_run(tmp_home.runs_dir, "2026-10-09-0800")
    make_run(tmp_home.runs_dir, "2026-10-09-0900", complete=False, kenya_ok=False)
    r = invoke(tmp_home)
    assert r.exit_code == 0 and "Stage timings" in r.stdout and "kenya_news" in r.stdout
    assert "Last 2 runs" in r.stdout
    one = invoke(tmp_home, "2026-10-09-0900")
    assert "incomplete" in one.stdout and "timeout" in one.stdout and "LLM" in one.stdout
    assert one.stdout.index("kenya_news") < one.stdout.index("cbk")  # slowest first
    assert invoke(tmp_home, "2026-10-09-9999").exit_code == 2


def test_report_json_shape(tmp_home):
    make_run(tmp_home.runs_dir, "2026-10-09-0800")
    make_run(tmp_home.runs_dir, "2026-10-09-0900")
    r = invoke(tmp_home, "--json", "--last", "1")
    data = json.loads(r.stdout)
    assert set(data) == {"runs", "aggregate"} and len(data["runs"]) == 1
    assert data["runs"][0]["run_id"] == "2026-10-09-0900"
    assert data["runs"][0]["llm"]["calls"] == 2
    assert data["aggregate"]["runs"] == 1


def test_report_json_empty_home_is_valid_json(tmp_home):
    r = runner.invoke(app, ["--home", str(tmp_home.root), "report", "--json"])
    data = json.loads(r.stdout)
    assert r.exit_code == 0 and data["runs"] == [] and data["aggregate"]["runs"] == 0


def test_report_shows_cached_and_hides_unconfigured_sources(tmp_home):
    tmp_home.sources_path.write_text(
        "kenya_news:\n  type: rss\n  url: https://k.ke/feed\n  kind: news\n", encoding="utf-8"
    )
    make_run(tmp_home.runs_dir, "2026-10-09-0800", kenya_cached=True)
    r = invoke(tmp_home)
    sources = r.stdout.split("Source reliability")[1]
    assert "kenya_news" in sources and "cached" in sources and "0.0s" not in sources
    assert "cbk" not in sources  # not configured any more
