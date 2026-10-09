"""End to end: init -> run (fixtures + scripted LLM) -> artifacts. No network."""

import json
import re

import httpx
import pytest
import tenacity
import yaml
from conftest import function_model_returning, mock_sources
from typer.testing import CliRunner

from kenya_data_engine.cli.app import app
from kenya_data_engine.config import load_sources
from kenya_data_engine.home import EngineHome
from kenya_data_engine.models import RadarResult, TopicList
from kenya_data_engine.runs import RunStore
from kenya_data_engine.synth.cluster import Cluster, ClusterOutput
from kenya_data_engine.synth.stage import SynthesizeStage

KEYS = {
    "DEEPSEEK_API_KEY": "sk-e2e-deepseek-0123456789",
    "TAVILY_API_KEY": "tvly-e2e-0123456789",
    "SERPER_API_KEY": "serper-e2e-0123456789",
    "JINA_API_KEY": "jina-e2e-0123456789",
}


def scripted_model():
    def out(prompt: str):
        if prompt.startswith("Signals:"):
            ids = [ln.split(" | ")[0] for ln in prompt.splitlines()[1:] if " | " in ln]
            clusters = [
                Cluster(
                    title=f"C{i}",
                    summary=f"sum {i}",
                    category="economy",
                    signal_ids=ids[2 * i : 2 * i + 2],
                )
                for i in range(4)
            ]
            clusters.append(
                Cluster(title="Ghost", summary="x", category="law", signal_ids=["nope"])
            )
            return ClusterOutput(clusters=clusters)
        n = int(re.search(r"Topic: C(\d)", prompt).group(1))
        return dict(
            data_ability=5 - n,
            wallet_impact=3,
            timeliness=3,
            clarity_gap=3,
            novelty=3,
            justification={},
            why_now=f"why {n}",
        )

    return function_model_returning(out)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("kenya_data_engine.http._WAIT", tenacity.wait_none())


def test_init_then_run_end_to_end(tmp_path, monkeypatch, respx_mock):
    home_dir = tmp_path / "home"
    monkeypatch.setenv("ENGINE_HOME", str(home_dir))
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    cli = CliRunner()

    r = cli.invoke(app, ["init", "--non-interactive", "--no-verify"])
    assert r.exit_code == 0, r.output

    home = EngineHome(home_dir)
    # Fixtures are dated Oct 2026: widen the window so the test does not depend on the clock.
    cfg = yaml.safe_load(home.config_path.read_text()) or {}
    cfg.setdefault("radar", {})["since_hours"] = 24 * 365 * 100
    home.config_path.write_text(yaml.safe_dump(cfg))

    mock_sources(respx_mock, home)
    # One source fails with a message that contains a secret: it must never reach disk.
    respx_mock.get(load_sources(home).specs["nation"].url).mock(
        side_effect=httpx.ConnectError(f"refused for {KEYS['TAVILY_API_KEY']}")
    )
    monkeypatch.setattr(
        "kenya_data_engine.cli.run.build_stages",
        lambda: [
            __import__("kenya_data_engine.radar.base", fromlist=["x"]).RadarStage(),
            SynthesizeStage(model=scripted_model()),
        ],
    )

    r = cli.invoke(app, ["run", "--top", "3"])
    assert r.exit_code == 0, r.output
    assert "Ranked topics" in r.stdout and "$" in r.stdout

    run = RunStore(home.runs_dir).latest()
    assert run is not None
    for name in ("signals.json", "topics.json", "trace.jsonl", "summary.json"):
        assert (run.dir / name).is_file(), name

    radar = RadarResult.model_validate_json((run.dir / "signals.json").read_text())
    topics = TopicList.model_validate_json((run.dir / "topics.json").read_text())
    assert len(radar.signals) > 10
    assert [e.adapter for e in radar.errors] == ["nation"]
    assert "***" in radar.errors[0].message

    assert 0 < len(topics.topics) <= 3
    scores = [t.final_score for t in topics.topics]
    assert scores == sorted(scores, reverse=True)
    ids = {s.id for s in radar.signals}
    assert all(set(t.signal_ids) <= ids for t in topics.topics)
    assert any("Ghost" in d for d in topics.dropped)
    assert json.loads((run.dir / "summary.json").read_text())["total_cost_usd"] > 0

    # No API-key value anywhere under runs/ (or in what the CLI printed).
    blobs = [p.read_bytes() for p in home.runs_dir.rglob("*") if p.is_file()]
    assert blobs
    for key in KEYS.values():
        assert all(key.encode() not in b for b in blobs), key
        assert key not in r.output

    # Resuming a finished run does no network work at all.
    calls = len(respx_mock.calls)
    r2 = cli.invoke(app, ["run", "--resume", run.run_id])
    assert r2.exit_code == 0, r2.output
    assert len(respx_mock.calls) == calls
