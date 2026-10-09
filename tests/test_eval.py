import json
from datetime import UTC, datetime

import pytest
from test_cli_research import cli  # noqa: F401  (fixture)
from test_orchestrator import brief_dict
from typer.testing import CliRunner

from kenya_data_engine.cli.app import app
from kenya_data_engine.errors import ConfigError
from kenya_data_engine.research.eval import (
    EvalRow,
    EvalStore,
    Scenario,
    judge,
    load_scenarios,
)
from kenya_data_engine.research.models import ResearchBrief, Scorecard
from kenya_data_engine.research.orchestrator import ResearchOutcome


def _outcome(ctx_run_id, verdict="supported", facts=2, usd=0.01, credits=3):
    brief = ResearchBrief.model_validate(brief_dict())
    return ResearchOutcome(
        run_id=ctx_run_id, brief=brief, statuses=[], claims=[], conflicts=[],
        verdict=verdict, verdict_reasons=[],
        scorecard=Scorecard(
            preset="standard", usd_spent=usd, usd_cap=0.15, credits_spent=credits,
            credits_cap=25, seconds=1.0, facts=facts, stopped_because="all needs satisfied",
        ),
        gaps=[], challenge_note=None, figures=None, stopped_because="all needs satisfied",
    )  # fmt: skip


def test_scenarios_load_and_merge(tmp_home):
    names = {s.name: s for s in load_scenarios(tmp_home)}
    assert set(names) == {"fuel", "cbk_rate", "weak"}
    assert names["fuel"].min_facts == 2 and names["fuel"].expect_verdict == [
        "supported",
        "reframed",
    ]
    assert names["cbk_rate"].max_credits == 10 and names["weak"].max_usd == 0.05
    (tmp_home.root / "eval.yaml").write_text(
        "scenarios:\n  weak:\n    max_usd: 0.01\n  mine:\n    topic: Maize prices\n"
    )
    merged = {s.name: s for s in load_scenarios(tmp_home)}
    assert merged["weak"].max_usd == 0.01 and merged["weak"].topic.startswith("Kenyans love")
    assert merged["mine"].topic == "Maize prices"
    (tmp_home.root / "eval.yaml").write_text("scenarios: [oops")
    with pytest.raises(ConfigError):
        load_scenarios(tmp_home)


def test_judge_expectations():
    sc = Scorecard(preset="lean", usd_spent=0.06, usd_cap=0.1, credits_spent=11, credits_cap=25,
                   seconds=1, facts=1)  # fmt: skip
    s = Scenario(name="x", topic="t", expect_verdict=["supported"], min_facts=2, max_usd=0.05,
                 max_credits=10)  # fmt: skip
    assert len(judge(s, "reject", sc)) == 4
    assert judge(Scenario(name="y", topic="t"), "anything", sc) == []
    ok = sc.model_copy(update={"facts": 2, "usd_spent": 0.05, "credits_spent": 10})
    assert judge(s, "supported", ok) == []


def test_store_previous(tmp_home):
    store = EvalStore(tmp_home.db_path)
    assert store.previous("fuel") is None
    sc = _outcome("r").scorecard
    for facts, passed in ((1, False), (3, True)):
        row = EvalRow(ts=datetime.now(UTC), engine_version="0.1.0", scenario="fuel",
                      scorecard=sc.model_copy(update={"facts": facts}), verdict="supported",
                      passed=passed)  # fmt: skip
        store.add(row)
    prev = store.previous("fuel")
    assert prev is not None and prev.scorecard.facts == 3 and prev.passed
    assert store.previous("weak") is None


def test_eval_records_and_compares(cli, monkeypatch, tmp_home):  # noqa: F811
    canned = {
        "Kenya fuel pump prices this month and the VAT on fuel": ("supported", 3, 0.04, 8),
        "Central Bank of Kenya base rate decision": ("supported", 0, 0.02, 12),  # fails twice
        "Kenyans love tea more than coffee": ("reject", 0, 0.01, 1),
    }
    calls = []

    async def fake_research(topic, rc, *, preset=None, models=None, **kw):
        verdict, facts, usd, credits = canned[topic.title]
        calls.append((topic.title, preset))
        return _outcome(rc.run.run_id, verdict, facts, usd, credits)

    monkeypatch.setattr("kenya_data_engine.cli.eval.research", fake_research)
    first = cli.invoke(app, ["eval", "--budget", "lean"])
    assert first.exit_code == 1, first.output  # cbk_rate failed
    assert len(calls) == 3 and {p for _, p in calls} == {"lean"}
    assert "fuel" in first.stdout and "pass" in first.stdout and "FAIL" in first.stdout
    assert "1 facts, wanted at least 1" not in first.stdout and "0 facts" in first.stdout

    canned["Kenya fuel pump prices this month and the VAT on fuel"] = ("reframed", 2, 0.05, 9)
    canned["Central Bank of Kenya base rate decision"] = ("supported", 2, 0.02, 5)
    second = cli.invoke(app, ["eval"])
    assert second.exit_code == 0, second.output
    print(second.stdout)
    assert "reframed" in second.stdout and "Was" in second.stdout  # compared with the previous run
    assert "(-1)" in second.stdout and "(+0.010)" in second.stdout
    rows = EvalStore(tmp_home.db_path)
    assert rows.previous("fuel").verdict == "reframed" and rows.previous("fuel").passed
    # a dossier was written for each scenario run
    assert len(list(tmp_home.briefs_dir.rglob("dossier.json"))) == 6

    only = cli.invoke(app, ["eval", "--only", "weak", "--json"])
    data = json.loads(only.stdout)
    assert [d["scenario"] for d in data] == ["weak"] and data[0]["passed"]
    bad = cli.invoke(app, ["eval", "--only", "nope"])
    assert bad.exit_code == 2 and "scenarios: " in bad.output


def test_eval_scenario_error_is_reported(cli, monkeypatch):  # noqa: F811
    async def boom(topic, rc, **kw):
        raise RuntimeError("provider down key=fake-deepseek")

    monkeypatch.setattr("kenya_data_engine.cli.eval.research", boom)
    r = cli.invoke(app, ["eval", "--only", "fuel"])
    assert r.exit_code == 1 and "provider down" in r.stdout
    assert "fake-deepseek" not in r.stdout and "fake-deepseek" not in r.output
    assert CliRunner().invoke(app, ["eval", "--help"]).exit_code == 0
