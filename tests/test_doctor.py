import json

import httpx
import pytest
import tenacity
from conftest import mock_sources
from typer.testing import CliRunner

from kenya_data_engine.cli import doctor as doctor_mod
from kenya_data_engine.cli.app import app
from kenya_data_engine.cli.doctor import Check, checks_table, run_checks, summary_line

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
    mock_sources(respx_mock, ctx.config)


async def test_doctor_all_healthy(respx_mock, ctx):
    mock_healthy(respx_mock, ctx)
    checks = by_name(await run_checks(ctx))
    assert {c.status for c in checks.values()} == {"ok"}
    assert checks["DeepSeek /models"].latency_ms is not None
    assert checks["cbk"].group == "sources" and "signals" in checks["cbk"].detail


async def test_doctor_missing_deepseek_key_fails(respx_mock, ctx_no_keys):
    respx_mock.route().mock(side_effect=httpx.ConnectError("down"))
    checks = by_name(await run_checks(ctx_no_keys))
    assert (
        checks["DeepSeek key"].status == "fail" and "engine init" in checks["DeepSeek key"].detail
    )
    assert checks["Search key"].status == "fail"
    assert checks["DeepSeek /models"].status == "fail"
    assert checks["Web search"].status == "fail"


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
    respx_mock.get(ctx.config.radar.feeds["nation"]).mock(
        side_effect=httpx.ConnectError("boom fake-tavily")
    )
    checks = by_name(await run_checks(ctx))  # must not raise
    assert checks["nation"].status == "fail"
    assert "fake-tavily" not in checks["nation"].detail  # secrets redacted
    assert checks["standard"].status == "ok"


async def test_doctor_empty_source_warns(respx_mock, ctx):
    mock_healthy(respx_mock, ctx)
    respx_mock.get(ctx.config.radar.feeds["nation"]).respond(200, content=b"not a feed")
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
    ]
    from rich.console import Console

    from kenya_data_engine.cli.ui import THEME

    c = Console(width=100, record=True, theme=THEME)
    c.print(checks_table(checks))
    c.print(summary_line(checks))
    out = c.export_text()
    assert "API keys" in out and "Radar sources" in out and "12 ms" in out
    assert "1 ok · 1 warn · 1 fail" in out


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
