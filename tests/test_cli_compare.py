import json

from compare_factory import topic, write_topics
from runs_factory import make_run
from typer.testing import CliRunner

from kenya_data_engine.cli.app import app

runner = CliRunner()


def invoke(home, *args):
    return runner.invoke(app, ["--home", str(home.root), "compare", *args])


def seed(home):
    write_topics(
        home.runs_dir,
        "2026-10-09-0800",
        topic("a1", "Fuel prices jump", ["s1", "s2"], score=4.2, wallet_impact=5),
        topic("a2", "M-Pesa fee changes", ["s9"], score=3.0),
    )
    write_topics(
        home.runs_dir,
        "2026-10-09-0900",
        topic("b1", "Petrol cost rise", ["s2", "s1"], score=3.8, wallet_impact=3),
        topic("b2", "Housing levy", ["s20"], score=3.5),
    )


def test_compare_prints_consensus_table_and_criteria(tmp_home):
    seed(tmp_home)
    r = invoke(tmp_home)
    assert r.exit_code == 0, r.stdout
    out = r.stdout
    assert "Fuel prices jump" in out and "2/2" in out and "4.00" in out
    assert "3.8" in out and "4.2" in out  # min-max range
    assert "strong" in out and "mixed" in out
    assert "wallet_impact" in out and "4.0" in out and "3-5" in out
    assert out.index("Fuel prices jump") < out.index("Housing levy")


def test_compare_json(tmp_home):
    seed(tmp_home)
    data = json.loads(invoke(tmp_home, "--json").stdout)
    assert data[0]["title"] == "Fuel prices jump" and data[0]["appearances"] == 2
    assert data[0]["criteria"]["wallet_impact"] == {"mean": 4.0, "min": 3, "max": 5}


def test_compare_named_runs_and_last(tmp_home):
    seed(tmp_home)
    write_topics(tmp_home.runs_dir, "2026-10-09-1000", topic("c1", "Fuel", ["s1"], score=4.0))
    named = json.loads(invoke(tmp_home, "2026-10-09-0800", "2026-10-09-0900", "--json").stdout)
    assert named[0]["runs"] == 2
    last2 = json.loads(invoke(tmp_home, "--last", "2", "--json").stdout)
    assert last2[0]["runs"] == 2 and "2026-10-09-0800" not in last2[0]["run_ids"]


def test_compare_skips_incomplete_runs_with_a_note(tmp_home):
    seed(tmp_home)
    make_run(tmp_home.runs_dir, "2026-10-09-1100", complete=False)
    r = invoke(tmp_home)
    assert r.exit_code == 0 and "skipped" in r.stdout and "2026-10-09-1100" in r.stdout
    assert "Fuel prices jump" in r.stdout


def test_compare_needs_two_runs(tmp_home):
    r = invoke(tmp_home)
    assert r.exit_code == 0 and "at least 2" in r.stdout and "engine run" in r.stdout
    write_topics(tmp_home.runs_dir, "2026-10-09-0800", topic("a", "Fuel", ["s1"]))
    assert "at least 2" in invoke(tmp_home).stdout
    j = invoke(tmp_home, "--json")
    assert (
        j.exit_code == 0
        and json.loads(j.stdout.split("\n", 1)[0] if "\n" in j.stdout else j.stdout) == []
    )


def test_compare_unknown_run_is_a_friendly_error(tmp_home):
    seed(tmp_home)
    assert invoke(tmp_home, "2026-10-09-9999").exit_code == 2
