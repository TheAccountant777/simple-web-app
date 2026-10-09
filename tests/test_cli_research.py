import json
from datetime import UTC, date, datetime

import pytest
from test_cli_run import RADAR, make_topic
from test_orchestrator import WB_BODY, WB_URL, models
from typer.testing import CliRunner

from kenya_data_engine.cli.app import app
from kenya_data_engine.cli.research import resolve_topic
from kenya_data_engine.errors import ConfigError
from kenya_data_engine.models import RadarResult, Signal, TopicList
from kenya_data_engine.research.memory import SourceMemory
from kenya_data_engine.research.models import DataNeed, DataSourceSpec
from kenya_data_engine.runs import RunStore


@pytest.fixture
def cli(tmp_home, monkeypatch, data_net):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_home.root))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek")
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily")
    return CliRunner()


def _make_run(tmp_home, when, topics=None, signals=None):
    run = RunStore(tmp_home.runs_dir).new_run(when)
    if topics is not None:
        run.write("topics", TopicList(topics=topics))
    if signals is not None:
        run.write("signals", signals)
    return run


def test_resolve_topic_rank_and_id_and_text(tmp_home):
    many = RadarResult(
        signals=[
            Signal(
                id=f"s{i}", kind="news", title=f"Sig {i}", source="x",
                url=f"https://x.ke/{i}", published_at=None,
            )
            for i in range(20)
        ],
        errors=[], collected_at=datetime(2026, 10, 9, tzinfo=UTC),
    )  # fmt: skip
    t1, t2 = make_topic(1, 4.0), make_topic(2, 3.0)
    t1.signal_ids = [f"s{i}" for i in range(20)] + ["missing"]
    first = _make_run(tmp_home, datetime(2026, 10, 8, 8, 0), [t1, t2], many)
    later = _make_run(tmp_home, datetime(2026, 10, 9, 8, 0), [t2], RADAR)
    _make_run(tmp_home, datetime(2026, 10, 9, 9, 0))  # a research run: no topics
    store = RunStore(tmp_home.runs_dir)

    ranked = resolve_topic("1", store)  # the latest run *with topics*
    assert ranked.title == "Topic number 2" and ranked.signals == ["T"]  # no url: title only
    by_id = resolve_topic(f"{first.run_id}:t1", store)
    assert by_id.title == "Topic number 1" and by_id.why_now == "because 1"
    assert len(by_id.signals) == 15 and by_id.signals[0] == "Sig 0 — https://x.ke/0"
    text = resolve_topic("  Kenya fuel prices  ", store)
    assert text.title == "Kenya fuel prices" and text.signals == []
    assert later.run_id in store.list()


def test_resolve_bad_rank_hint(tmp_home):
    _make_run(
        tmp_home, datetime(2026, 10, 9, 8, 0), [make_topic(1, 4.0), make_topic(2, 3.0)], RADAR
    )
    store = RunStore(tmp_home.runs_dir)
    with pytest.raises(ConfigError) as e:
        resolve_topic("7", store)
    assert "valid ranks: 1-2" in e.value.hint
    with pytest.raises(ConfigError) as e:
        resolve_topic("0", store)
    assert "valid ranks" in e.value.hint
    run_id = store.list()[0]
    with pytest.raises(ConfigError) as e:
        resolve_topic(f"{run_id}:nope", store)
    assert "t1, t2" in e.value.hint
    with pytest.raises(ConfigError):
        resolve_topic("", store)
    empty = RunStore(tmp_home.runs_dir.parent / "none")
    with pytest.raises(ConfigError) as e:
        resolve_topic("1", empty)
    assert "engine run" in e.value.hint


@pytest.fixture
def seams(monkeypatch):
    m, planner = models()
    monkeypatch.setattr("kenya_data_engine.cli.research.default_models", lambda: m)
    return planner


def test_research_cli_end_to_end(cli, seams, respx_mock, tmp_home):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    r = cli.invoke(app, ["research", "Kenya inflation", "--budget", "lean"])
    assert r.exit_code == 0, r.output
    out = r.stdout
    assert "Researching: Kenya inflation" in out
    assert "satisfied" in out and "fact" in out and "7.7%" in out  # need and claim lines
    assert "budget $" in out and "searches" in out  # ledger gauge
    assert "Research" in out and "supported" in out and "1 facts" in out
    paths = list(tmp_home.briefs_dir.rglob("README.md"))
    assert len(paths) == 1 and str(paths[0].parent).replace("\n", "") in out.replace("\n", "")

    listed = cli.invoke(app, ["dossiers", "list"])
    assert listed.exit_code == 0 and "kenya-inflation" in listed.stdout
    assert "supported" in listed.stdout and "$0." in listed.stdout
    shown = cli.invoke(app, ["dossiers", "show", "latest"])
    assert shown.exit_code == 0 and "15-minute check" in shown.stdout
    by_name = cli.invoke(app, ["dossiers", "show", "01-kenya-inflation"])
    assert by_name.exit_code == 0 and "Top facts" in by_name.stdout
    by_path = cli.invoke(app, ["dossiers", "show", str(paths[0].parent)])
    assert by_path.exit_code == 0
    missing = cli.invoke(app, ["dossiers", "show", "99-nothing"])
    assert missing.exit_code == 2 and "no dossier matches" in missing.output
    as_json = json.loads(cli.invoke(app, ["dossiers", "list", "--json"]).stdout)
    assert as_json[0]["slug"] == "kenya-inflation" and as_json[0]["facts"] == 1

    rep = cli.invoke(app, ["report"])
    assert rep.exit_code == 0, rep.output
    assert "Research" in rep.stdout and "Facts/$" in rep.stdout
    rep_json = json.loads(cli.invoke(app, ["report", "--json"]).stdout)
    assert rep_json["research"][0]["verdict"] == "supported"

    mem = cli.invoke(app, ["memory", "list"])
    assert (
        mem.exit_code == 0
        and "Kenya inflation" in mem.stdout
        and "Sources that worked" in mem.stdout
    )


def test_dossiers_list_and_show(cli, tmp_home):
    empty = cli.invoke(app, ["dossiers", "list"])
    assert empty.exit_code == 0 and "No dossiers yet" in empty.stdout
    assert json.loads(cli.invoke(app, ["dossiers", "list", "--json"]).stdout) == []
    gone = cli.invoke(app, ["dossiers", "show", "latest"])
    assert gone.exit_code == 2 and "no dossiers yet" in gone.output


def test_research_json_and_plan_only(cli, seams, respx_mock, tmp_home):
    r = cli.invoke(app, ["research", "Kenya inflation", "--plan-only", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)  # stdout is pure JSON
    assert data["dossier"] is None and data["stopped_because"] == "plan only"
    assert not list(tmp_home.briefs_dir.rglob("README.md"))
    assert seams.calls == 1
    text = cli.invoke(app, ["research", "Kenya inflation", "--plan-only"])
    assert (
        text.exit_code == 0
        and "Data needs" in text.stdout
        and "How high is inflation?" in text.stdout
    )


def test_research_resume_reads_topic_from_checkpoint(cli, seams, respx_mock, tmp_home):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    assert cli.invoke(app, ["research", "Kenya inflation"]).exit_code == 0
    run_id = RunStore(tmp_home.runs_dir).list()[0]
    r = cli.invoke(app, ["research", "--resume", run_id])
    assert r.exit_code == 0, r.output
    assert "Researching: Kenya inflation" in r.stdout and "resumed" in r.stdout
    assert seams.calls == 1  # the planner was not asked again
    assert len(list(tmp_home.briefs_dir.rglob("README.md"))) == 2


def test_research_errors_exit_1(cli, monkeypatch, tmp_home):
    r = cli.invoke(app, ["research"])
    assert r.exit_code == 1 and "give a TOPIC" in r.output
    r = cli.invoke(app, ["research", "x", "--budget", "huge"])
    assert r.exit_code == 1 and "lean, standard, deep" in r.output
    r = cli.invoke(app, ["research", "--resume", "2020-01-01-0000"])
    assert r.exit_code == 1 and "run not found" in r.output
    r = cli.invoke(app, ["research", "9"])
    assert r.exit_code == 1 and "engine run" in r.output
    monkeypatch.delenv("TAVILY_API_KEY")
    r = cli.invoke(app, ["research", "x"])
    assert r.exit_code == 1 and "no search key" in r.output
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    r = cli.invoke(app, ["research", "x"])
    assert r.exit_code == 1 and "DEEPSEEK_API_KEY" in r.output


def test_memory_list_forget(cli, tmp_home):
    mem = cli.invoke(app, ["memory", "list"])
    assert mem.exit_code == 0 and "Nothing remembered" in mem.stdout
    need = DataNeed(id="n1", kind="series", question="q", metric="super petrol", unit="KES/L",
                    frequency="monthly", entities=["Nairobi"])  # fmt: skip
    spec = DataSourceSpec(need="n1", via="registry", registry_key="wb:FP.CPI.TOTL.ZG",
                          publisher="World Bank", why="w")  # fmt: skip
    SourceMemory(tmp_home.db_path).record(need, spec, True, date(2026, 10, 9))
    listed = cli.invoke(app, ["memory", "list"])
    assert "super petrol|nairobi|kes/l|monthly" in listed.stdout
    assert "wb:FP.CPI.TOTL.ZG" in listed.stdout
    as_json = json.loads(cli.invoke(app, ["memory", "list", "--json"]).stdout)
    assert as_json["sources"][0]["successes"] == 1 and as_json["topics"] == []
    gone = cli.invoke(app, ["memory", "forget", "nothing|here"])
    assert gone.exit_code == 1 and "nothing remembered" in gone.output
    ok = cli.invoke(app, ["memory", "forget", "super petrol|nairobi|kes/l|monthly"])
    assert ok.exit_code == 0 and "Forgot 1 remembered source." in ok.stdout
    assert SourceMemory(tmp_home.db_path).entries() == []


def test_help_lists_new_commands():
    runner = CliRunner()
    top = runner.invoke(app, ["--help"])
    for word in ("research", "dossiers", "memory", "eval"):
        assert word in top.stdout
    for cmd in (["research"], ["dossiers"], ["memory"], ["eval"]):
        assert runner.invoke(app, [*cmd, "--help"]).exit_code == 0
