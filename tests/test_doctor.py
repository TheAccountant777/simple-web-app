import json

import httpx
import pytest
import tenacity
from conftest import mock_sources
from typer.testing import CliRunner

from kenya_data_engine.cli import doctor as doctor_mod
from kenya_data_engine.cli.app import app
from kenya_data_engine.cli.doctor import Check, checks_table, run_checks, summary_line
from kenya_data_engine.config import load_sources
from kenya_data_engine.health import HealthStore, failing_label

MODELS = "https://api.deepseek.com/models"
TAVILY = "https://api.tavily.com/search"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("kenya_data_engine.http._WAIT", tenacity.wait_none())


def by_name(checks):
    return {c.name: c for c in checks}


def mock_healthy(respx_mock, ctx):
    respx_mock.get(MODELS).respond(200, json={"data": [{"id": "deepseek-flash"}]})
    respx_mock.post(TAVILY).respond(
        200,
        json={
            "results": [{"title": "Kenya inflation eases", "url": "https://a.ke", "content": "x"}]
        },
    )
    mock_sources(respx_mock, ctx.home)


async def test_doctor_all_healthy(respx_mock, ctx):
    mock_healthy(respx_mock, ctx)
    checks = by_name(await run_checks(ctx))
    assert {c.status for c in checks.values()} == {"ok"}, [
        c for c in checks.values() if c.status != "ok"
    ]
    assert checks["DeepSeek /models"].latency_ms is not None
    assert checks["cbk_news"].group == "sources" and "signals" in checks["cbk_news"].detail


async def test_doctor_missing_deepseek_key_fails(respx_mock, ctx_no_keys):
    respx_mock.route().mock(side_effect=httpx.ConnectError("down"))
    checks = by_name(await run_checks(ctx_no_keys))
    assert (
        checks["DeepSeek key"].status == "fail" and "engine init" in checks["DeepSeek key"].detail
    )
    assert checks["Search key"].status == "fail"
    assert checks["DeepSeek /models"].status == "skip"
    assert checks["Web search"].status == "skip"


async def test_doctor_single_search_key_warns(respx_mock, ctx):
    ctx.secrets.serper_api_key = None
    mock_healthy(respx_mock, ctx)
    checks = by_name(await run_checks(ctx))
    assert checks["Search key"].status == "warn" and "Tavily" in checks["Search key"].detail


async def test_doctor_model_not_listed_warns(respx_mock, ctx):
    mock_healthy(respx_mock, ctx)
    respx_mock.get(MODELS).respond(200, json={"data": [{"id": "other"}]})
    c = by_name(await run_checks(ctx))["DeepSeek /models"]
    assert c.status == "warn" and "model not listed" in c.detail


@pytest.mark.parametrize("status,expect", [(401, "rejected"), (500, "HTTP 500")])
async def test_doctor_llm_failures(respx_mock, ctx, status, expect):
    mock_healthy(respx_mock, ctx)
    respx_mock.get(MODELS).respond(status)
    c = by_name(await run_checks(ctx))["DeepSeek /models"]
    assert c.status == "fail" and expect in c.detail


async def test_doctor_source_exception_becomes_fail_check(respx_mock, ctx):
    mock_healthy(respx_mock, ctx)
    respx_mock.get(load_sources(ctx.home).specs["nation"].url).mock(
        side_effect=httpx.ConnectError("boom fake-tavily")
    )
    checks = by_name(await run_checks(ctx))  # must not raise
    assert checks["nation"].status == "fail"
    assert "fake-tavily" not in checks["nation"].detail  # secrets redacted
    assert checks["standard"].status == "ok"


async def test_doctor_shows_consecutive_failures_and_records_health(respx_mock, ctx):
    mock_healthy(respx_mock, ctx)
    nation = load_sources(ctx.home).specs["nation"].url
    respx_mock.get(nation).respond(404)
    first = by_name(await run_checks(ctx))["nation"]
    second = by_name(await run_checks(ctx))["nation"]
    assert first.status == "fail" and failing_label(1) in first.detail
    assert failing_label(2) in second.detail and "404" in second.detail
    rows = HealthStore(ctx.home.db_path).all()
    assert rows["nation"].consecutive_failures == 2 and rows["standard"].consecutive_failures == 0


async def test_doctor_source_timeout_uses_source_timeout(respx_mock, ctx):
    import asyncio

    mock_healthy(respx_mock, ctx)
    ctx.config.radar.source_timeout_s = 0.05

    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, text="x")

    respx_mock.get(load_sources(ctx.home).specs["nation"].url).mock(side_effect=slow)
    c = by_name(await run_checks(ctx))["nation"]
    assert c.status == "fail" and "timed out after 0.05s" in c.detail


async def test_doctor_warns_when_config_still_has_legacy_radar_sources(respx_mock, ctx):
    mock_healthy(respx_mock, ctx)
    clean = by_name(await run_checks(ctx))
    assert "config.yaml" not in clean
    ctx.home.config_path.write_text("radar:\n  feeds: {a: https://a.ke}\n  enabled: [rss]\n")
    c = by_name(await run_checks(ctx))["config.yaml"]
    assert c.status == "warn" and c.group == "config"
    assert "radar.enabled" in c.detail and "radar.feeds" in c.detail
    assert "sources.yaml" in c.detail and "engine init --reset-config" in c.detail
    assert await run_checks(ctx)  # still produces the normal checks


async def test_doctor_empty_source_warns(respx_mock, ctx):
    mock_healthy(respx_mock, ctx)
    respx_mock.get(load_sources(ctx.home).specs["nation"].url).respond(200, content=b"not a feed")
    assert by_name(await run_checks(ctx))["nation"].status == "warn"


async def test_doctor_search_empty_warns(respx_mock, ctx):
    mock_healthy(respx_mock, ctx)
    respx_mock.post(TAVILY).respond(200, json={"results": []})
    assert by_name(await run_checks(ctx))["Web search"].status == "warn"


async def test_doctor_timeout_is_fail(ctx, monkeypatch):
    import asyncio

    monkeypatch.setattr(doctor_mod, "CHECK_TIMEOUT_S", 0.01)

    async def slow():
        await asyncio.sleep(1)
        return "ok", ""

    c = await doctor_mod._timed(ctx, "slow", "sources", slow)
    assert c.status == "fail" and "timed out" in c.detail


def test_render_table_and_summary():
    checks = [
        Check(name="DeepSeek key", group="keys", status="ok", detail="set"),
        Check(name="nation", group="sources", status="warn", latency_ms=12, detail="0 signals"),
        Check(name="cbk", group="sources", status="fail", latency_ms=3, detail="down"),
        Check(name="Web search", group="search", status="skip", detail="no key"),
    ]
    from rich.console import Console

    from kenya_data_engine.cli.ui import THEME

    c = Console(width=100, record=True, theme=THEME)
    c.print(checks_table(checks))
    c.print(summary_line(checks))
    out = c.export_text()
    assert "API keys" in out and "Radar sources" in out and "12 ms" in out
    assert "1 ok · 1 warn · 1 fail · 1 skipped" in out


@pytest.fixture
def cli_no_keys(tmp_home, monkeypatch):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_home.root))
    return CliRunner()


def test_doctor_exit_code_1_on_fail(cli_no_keys, respx_mock):
    respx_mock.route().mock(side_effect=httpx.ConnectError("down"))
    r = cli_no_keys.invoke(app, ["doctor"])
    assert r.exit_code == 1 and "Traceback" not in r.stdout
    assert "engine init" in r.stdout and "fail" in r.stdout


def test_doctor_json(cli_no_keys, monkeypatch):
    async def fake(ctx):
        return [Check(name="x", group="keys", status="ok", detail="d")]

    monkeypatch.setattr(doctor_mod, "run_checks", fake)
    r = cli_no_keys.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    assert [Check.model_validate(c).name for c in json.loads(r.stdout)] == ["x"]


def test_doctor_leaves_no_run_behind(cli_no_keys, monkeypatch, tmp_home):
    async def fake(ctx):
        return []

    monkeypatch.setattr(doctor_mod, "run_checks", fake)
    cli_no_keys.invoke(app, ["doctor"])
    assert list(tmp_home.runs_dir.iterdir()) == []


def test_doctor_exit_code_ignores_skips(cli_no_keys, monkeypatch):
    async def fake(ctx):
        return [Check(name="x", group="llm", status="skip", detail="no key")]

    monkeypatch.setattr(doctor_mod, "run_checks", fake)
    assert cli_no_keys.invoke(app, ["doctor"]).exit_code == 0
