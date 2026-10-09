import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from kenya_data_engine.cli.app import app
from kenya_data_engine.models import (
    AdapterError,
    RadarResult,
    Signal,
    Topic,
    TopicList,
    TopicScores,
)
from kenya_data_engine.runs import RunStore


def make_topic(i: int, score: float) -> Topic:
    return Topic(
        id=f"t{i}",
        title=f"Topic number {i}",
        summary="s",
        why_now=f"because {i}",
        category="economy",
        signal_ids=["s0"],
        scores=TopicScores(
            data_ability=3,
            wallet_impact=3,
            timeliness=3,
            clarity_gap=3,
            novelty=3,
            justification={},
        ),
        final_score=score,
    )


RADAR = RadarResult(
    signals=[
        Signal(id="s0", kind="news", title="T", source="x", url=None, published_at=None),
    ],
    errors=[AdapterError(adapter="cbk", message="selector matched nothing")],
    collected_at=datetime(2026, 10, 9, tzinfo=UTC),
)


class FakeRadar:
    name = "radar"
    output_name = "signals"
    output_type = RadarResult
    calls = 0

    async def run(self, ctx, inp):
        type(self).calls += 1
        return RADAR

    def describe(self, output):
        return f"{len(output.signals)} signals · {len(output.errors)} sources failed"


class FakeSynth:
    name = "synthesize"
    output_name = "topics"
    output_type = TopicList

    async def run(self, ctx, inp):
        ctx.tracer.record_llm("synthesize_score", "score", 1000, 500, 10)

        topics = [make_topic(1, 4.2), make_topic(2, 3.1), make_topic(3, 2.0)]
        return TopicList(
            topics=topics[: ctx.config.top_n],
            dropped=["Foo: empty cluster [x]"],
            budget_exhausted=False,
        )


class BoomStage:
    name = "boom"
    output_name = "topics"
    output_type = TopicList

    async def run(self, ctx, inp):
        raise RuntimeError("kaput")


@pytest.fixture
def cli(tmp_home, monkeypatch):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_home.root))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek")
    return CliRunner()


@pytest.fixture
def cli_no_keys(tmp_home, monkeypatch):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_home.root))
    return CliRunner()


@pytest.fixture
def fake_stages(monkeypatch):
    FakeRadar.calls = 0
    monkeypatch.setattr(
        "kenya_data_engine.cli.run.build_stages", lambda: [FakeRadar(), FakeSynth()]
    )
    monkeypatch.setattr("kenya_data_engine.cli.stage.RadarStage", FakeRadar)
    monkeypatch.setattr("kenya_data_engine.cli.stage.SynthesizeStage", FakeSynth)


@pytest.fixture
def boom_stage(monkeypatch):
    monkeypatch.setattr("kenya_data_engine.cli.run.build_stages", lambda: [BoomStage()])


def test_run_prints_ranked_table(cli, fake_stages):
    r = cli.invoke(app, ["run"])
    assert r.exit_code == 0, r.output
    out = r.stdout
    assert out.index("Topic number 1") < out.index("Topic number 2") < out.index("Topic number 3")
    assert "$" in out and "Run summary" in out
    assert "cbk" in out and "selector matched nothing" in out  # adapter error note
    assert "empty cluster [x]" in out  # dropped note, markup-safe


def test_run_top_override(cli, fake_stages):
    r = cli.invoke(app, ["run", "--top", "1"])
    assert "Topic number 1" in r.stdout and "Topic number 2" not in r.stdout


def test_missing_key_is_friendly(cli_no_keys):  # Review Focus 1
    r = cli_no_keys.invoke(app, ["run"])
    assert r.exit_code == 2 and "engine init" in r.stdout and "Traceback" not in r.stdout
    assert "DEEPSEEK_API_KEY" in r.stdout


def test_unexpected_error_hides_traceback_by_default(cli, boom_stage):
    r = cli.invoke(app, ["run"])
    assert r.exit_code == 1
    assert "unexpected error: kaput" in r.stdout and "-v" in r.stdout
    assert "Traceback" not in r.stdout


def test_verbose_shows_traceback_on_unexpected(cli, boom_stage):
    r = cli.invoke(app, ["-v", "run"])
    assert r.exit_code == 1 and "Traceback" in r.stdout and "kaput" in r.stdout


def test_stage_synthesize_requires_input(cli, fake_stages):
    r = cli.invoke(app, ["stage", "synthesize"])
    assert r.exit_code == 2
    assert "synthesize needs --input or --run" in r.stdout and "--input" in r.stdout


def test_run_json_output_is_valid(cli, fake_stages):
    r = cli.invoke(app, ["run", "--json"])
    assert r.exit_code == 0, r.output
    tl = TopicList.model_validate_json(r.stdout)
    assert [t.id for t in tl.topics] == ["t1", "t2", "t3"]


def test_json_errors_go_to_stderr(cli_no_keys):
    r = cli_no_keys.invoke(app, ["run", "--json"])
    assert r.exit_code == 2 and r.stdout == "" and "engine init" in r.stderr


def test_resume_flag_skips_radar(cli, fake_stages, tmp_home):
    store = RunStore(tmp_home.runs_dir)
    handle = store.new_run(datetime(2026, 10, 9, 8, 0))
    handle.write("signals", RADAR)
    r = cli.invoke(app, ["run", "--resume", handle.run_id])
    assert r.exit_code == 0, r.output
    assert FakeRadar.calls == 0
    assert (handle.dir / "topics.json").exists()


def test_resume_unknown_run_is_friendly(cli, fake_stages):
    r = cli.invoke(app, ["run", "--resume", "1999-01-01-0000"])
    assert r.exit_code == 2 and "run not found" in r.stdout


def test_quiet_hides_footer(cli, fake_stages):
    r = cli.invoke(app, ["-q", "run"])
    assert r.exit_code == 0 and "Run summary" not in r.stdout and "Topic number 1" in r.stdout


def test_stage_radar_writes_signals(cli, fake_stages, tmp_home):
    r = cli.invoke(app, ["stage", "radar"])
    assert r.exit_code == 0, r.output
    run = RunStore(tmp_home.runs_dir).latest()
    assert run is not None and (run.dir / "signals.json").exists()
    assert "signals.json" in r.stdout.replace("\n", "")


def test_stage_synthesize_from_run_and_input(cli, fake_stages, tmp_home, tmp_path):
    handle = RunStore(tmp_home.runs_dir).new_run(datetime(2026, 10, 9, 8, 0))
    handle.write("signals", RADAR)
    r = cli.invoke(app, ["stage", "synthesize", "--run", handle.run_id])
    assert r.exit_code == 0, r.output
    assert (handle.dir / "topics.json").exists()

    f = tmp_path / "in.json"
    f.write_text(RADAR.model_dump_json())
    r = cli.invoke(app, ["stage", "synthesize", "--input", str(f)])
    assert r.exit_code == 0, r.output
    latest = RunStore(tmp_home.runs_dir).latest()
    assert latest is not None and json.loads((latest.dir / "topics.json").read_text())["topics"]


def test_stage_synthesize_bad_inputs(cli, fake_stages, tmp_home, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert cli.invoke(app, ["stage", "synthesize", "--input", str(bad)]).exit_code == 2
    assert (
        cli.invoke(app, ["stage", "synthesize", "--input", str(tmp_path / "no.json")]).exit_code
        == 2
    )
    handle = RunStore(tmp_home.runs_dir).new_run(datetime(2026, 10, 9, 8, 0))
    r = cli.invoke(app, ["stage", "synthesize", "--run", handle.run_id])
    assert r.exit_code == 2 and "no usable signals.json" in r.stdout


def test_help_has_examples(cli):
    for args in (["--help"], ["run", "--help"], ["stage", "--help"]):
        r = cli.invoke(app, args)
        assert r.exit_code == 0 and "engine " in r.stdout
    assert "--resume" in cli.invoke(app, ["run", "--help"]).stdout


def test_stage_radar_on_run_removes_stale_topics(cli, fake_stages, tmp_home):
    handle = RunStore(tmp_home.runs_dir).new_run(datetime(2026, 10, 9, 8, 0))
    handle.write("topics", TopicList(topics=[make_topic(9, 1.0)]))
    r = cli.invoke(app, ["stage", "radar", "--run", handle.run_id])
    assert r.exit_code == 0, r.output
    assert (handle.dir / "signals.json").exists() and not (handle.dir / "topics.json").exists()


def test_stage_rejects_input_with_run(cli, fake_stages, tmp_home, tmp_path):
    handle = RunStore(tmp_home.runs_dir).new_run(datetime(2026, 10, 9, 8, 0))
    f = tmp_path / "in.json"
    f.write_text(RADAR.model_dump_json())
    r = cli.invoke(app, ["stage", "synthesize", "--input", str(f), "--run", handle.run_id])
    assert r.exit_code == 2 and "--input" in r.stdout and "--run" in r.stdout


def test_run_json_prints_notes_as_warnings_on_stderr(cli, fake_stages):
    r = cli.invoke(app, ["run", "--json"])
    assert r.exit_code == 0, r.output
    TopicList.model_validate_json(r.stdout)  # stdout stays pure JSON
    assert "cbk" in r.stderr and "selector matched nothing" in r.stderr
    assert "empty cluster [x]" in r.stderr


def test_run_json_budget_notice_on_stderr(cli, monkeypatch):
    class Exhausted(FakeSynth):
        async def run(self, ctx, inp):
            return TopicList(topics=[], budget_exhausted=True)

    monkeypatch.setattr(
        "kenya_data_engine.cli.run.build_stages", lambda: [FakeRadar(), Exhausted()]
    )
    r = cli.invoke(app, ["run", "--json"])
    assert "Budget exhausted" in r.stderr and "Budget exhausted" not in r.stdout


def test_progress_rows_show_stage_counts(cli, fake_stages):
    r = cli.invoke(app, ["run"])
    assert "1 signals · 1 sources failed" in r.output
