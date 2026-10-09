import json
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest
import tenacity
from conftest import mock_sources
from typer.testing import CliRunner

from kenya_data_engine.cache import Cache
from kenya_data_engine.cli.app import app
from kenya_data_engine.config import load_sources
from kenya_data_engine.health import HealthStore, failing_label


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("kenya_data_engine.http._WAIT", tenacity.wait_none())


@pytest.fixture
def cli(tmp_home, monkeypatch):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_home.root))
    return CliRunner()


def jlist(cli, *args):
    r = cli.invoke(app, ["sources", "list", "--json", *args])
    assert r.exit_code == 0, r.output
    return {row["name"]: row for row in json.loads(r.stdout)}


def jtest(cli, *args):
    r = cli.invoke(app, ["sources", "test", "--json", *args])
    return r, {x["name"]: x for x in json.loads(r.stdout)}


def test_list_text_shows_every_source_with_health(cli):
    r = cli.invoke(app, ["sources", "list"])
    assert r.exit_code == 0, r.output
    for name in ("nation", "google_trends", "cbk_news", "parliament", "reddit_kenya"):
        assert name in r.stdout
    assert "never run" in r.stdout and "listing" in r.stdout and "rss" in r.stdout


def test_list_json_is_stable_and_complete(cli, tmp_home):
    rows = jlist(cli)
    assert set(rows) == set(load_sources(tmp_home).specs)
    row = rows["nation"]
    assert list(row) == [
        "name",
        "type",
        "kind",
        "enabled",
        "url",
        "valid",
        "error",
        "health",
        "consecutive_failures",
        "last_ok_at",
        "last_error",
        "last_signal_count",
    ]
    assert row["health"] == "never_run" and row["valid"] is True and row["enabled"] is True
    assert rows["google_trends"]["kind"] == "attention"


def test_list_reflects_health_disabled_and_invalid(cli, tmp_home):
    h = HealthStore(tmp_home.db_path)
    h.record_ok("nation", 4)
    h.record_failure("standard", "404 for x")
    h.record_failure("standard", "404 for x")
    tmp_home.sources_path.write_text(
        "kenyans:\n  enabled: false\nbroken:\n  type: rss\n  kind: nope\n"
    )
    rows = jlist(cli)
    assert rows["nation"]["health"] == "ok" and rows["nation"]["last_signal_count"] == 4
    assert rows["standard"]["health"] == "failing" and rows["standard"]["consecutive_failures"] == 2
    assert rows["standard"]["last_error"] == "404 for x"
    assert rows["kenyans"]["enabled"] is False
    assert rows["broken"]["valid"] is False and rows["broken"]["health"] == "invalid"
    assert "kind" in rows["broken"]["error"]
    text = cli.invoke(app, ["sources", "list"]).stdout
    assert failing_label(2) in text and "invalid" in text


def test_test_one_source_ok(cli, respx_mock, tmp_home):
    mock_sources(respx_mock, tmp_home)
    r, out = jtest(cli, "cbk_news")
    assert r.exit_code == 0, r.output
    res = out["cbk_news"]
    assert list(res) == [
        "name",
        "status",
        "latency_ms",
        "item_count",
        "items",
        "error",
        "hint",
    ]
    assert res["status"] == "ok" and res["item_count"] == 3 and res["error"] is None
    assert res["latency_ms"] >= 0
    item = res["items"][0]
    assert list(item) == ["title", "date", "url"]
    assert item["title"].startswith("cbk_news headline") and item["url"].startswith("https://")
    assert item["date"].startswith(str(datetime.now(UTC).year))
    assert HealthStore(tmp_home.db_path).all()["cbk_news"].consecutive_failures == 0


def test_test_text_output(cli, respx_mock, tmp_home):
    mock_sources(respx_mock, tmp_home)
    r = cli.invoke(app, ["sources", "test", "nation"])
    assert r.exit_code == 0
    assert "nation" in r.stdout and "3 items" in r.stdout and "nation headline 0" in r.stdout
    assert "https://nation.example.ke/0" in r.stdout


def test_test_all_mixed_results_exit_1_and_never_aborts(cli, respx_mock, tmp_home):
    mock_sources(respx_mock, tmp_home)
    respx_mock.get("https://nation.africa/kenya/rss.xml").respond(404)
    respx_mock.get("https://www.centralbank.go.ke/news/").respond(200, text="<html>changed</html>")
    respx_mock.get("https://techcabal.com/feed/").mock(side_effect=httpx.ConnectError("refused"))
    respx_mock.get("https://www.kbc.co.ke/category/business/feed/").respond(
        200, content=b"<rss version='2.0'><channel/></rss>"
    )
    tmp_home.sources_path.write_text(
        "kenyans:\n  enabled: false\nbroken:\n  type: rss\n  kind: nope\n"
    )
    r, out = jtest(cli, "--all")
    assert r.exit_code == 1
    assert "kenyans" not in out  # --all skips disabled sources
    assert out["standard"]["status"] == "ok"
    assert out["nation"]["status"] == "fail" and "404" in out["nation"]["error"]
    assert out["cbk_news"]["status"] == "fail"
    assert "selector matched nothing" in out["cbk_news"]["error"]
    assert "engine sources test cbk_news" in out["cbk_news"]["hint"]
    assert out["techcabal"]["status"] == "fail"
    assert out["kbc_business"]["status"] == "empty" and out["kbc_business"]["item_count"] == 0
    assert out["broken"]["status"] == "fail" and "invalid source" in out["broken"]["error"]
    assert "calendar" not in out
    rows = jlist(cli)
    assert rows["nation"]["health"] == "failing" and rows["standard"]["health"] == "ok"


def test_test_all_exit_0_when_everything_is_ok(cli, respx_mock, tmp_home):
    mock_sources(respx_mock, tmp_home)
    r, out = jtest(cli, "--all")
    enabled = [n for n, s in load_sources(tmp_home).specs.items() if s.enabled]
    assert r.exit_code == 0 and set(out) == set(enabled)
    assert {x["status"] for x in out.values()} == {"ok"}


def test_test_bypasses_the_cache_and_shows_five_items(cli, respx_mock, tmp_home):
    url = "https://x.ke/feed"
    tmp_home.sources_path.write_text(f"x:\n  type: rss\n  url: {url}\n  kind: news\n")
    Cache(tmp_home.db_path).put(url, b"stale, not a feed", "text/xml", 24)
    now = format_datetime(datetime.now(UTC))
    items = "".join(
        f"<item><title>T{i}</title><link>https://x.ke/{i}</link><pubDate>{now}</pubDate></item>"
        for i in range(8)
    )
    route = respx_mock.get(url).respond(
        200, content=f"<rss version='2.0'><channel>{items}</channel></rss>".encode()
    )
    _, out = jtest(cli, "x")
    assert route.call_count == 1  # the stale cached copy was ignored
    assert out["x"]["item_count"] == 8 and len(out["x"]["items"]) == 5


def test_test_can_check_a_disabled_source_by_name(cli, respx_mock, tmp_home):
    mock_sources(respx_mock, tmp_home)
    tmp_home.sources_path.write_text("nation:\n  enabled: false\n")
    r, out = jtest(cli, "nation")
    assert r.exit_code == 0 and out["nation"]["status"] == "ok"


def test_test_error_text_shows_message_and_hint(cli, respx_mock, tmp_home):
    respx_mock.get("https://nation.africa/kenya/rss.xml").respond(404)
    r = cli.invoke(app, ["sources", "test", "nation"])
    assert r.exit_code == 1
    assert "fail" in r.stdout and "404 for https://nation.africa/kenya/rss.xml" in r.stdout


def test_unknown_name_and_bad_usage_exit_2(cli):
    r = cli.invoke(app, ["sources", "test", "nope"])
    assert (
        r.exit_code == 2
        and "unknown source `nope`" in r.stdout
        and "engine sources list" in r.stdout
    )
    assert cli.invoke(app, ["sources", "test"]).exit_code == 2
    assert cli.invoke(app, ["sources", "test", "nation", "--all"]).exit_code == 2


def test_sources_commands_leave_no_run_behind(cli, respx_mock, tmp_home):
    mock_sources(respx_mock, tmp_home)
    cli.invoke(app, ["sources", "test", "nation"])
    assert list(tmp_home.runs_dir.iterdir()) == []
