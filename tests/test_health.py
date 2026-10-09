import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from conftest import mock_sources

from kenya_data_engine.errors import FetchError
from kenya_data_engine.health import HealthStore
from kenya_data_engine.radar.base import build_adapters, fetch_with_timeout, run_radar

T0 = datetime(2000, 1, 1, tzinfo=UTC)


def test_never_run_source_has_no_row(tmp_path):
    assert HealthStore(tmp_path / "e.db").all() == {}


def test_ok_then_failures_then_recovery(tmp_path):
    h = HealthStore(tmp_path / "e.db")
    h.record_ok("a", 7)
    row = h.all()["a"]
    assert (row.consecutive_failures, row.last_signal_count, row.last_error) == (0, 7, None)
    assert row.last_ok_at is not None and row.updated_at >= row.last_ok_at
    assert h.record_failure("a", "boom") == 1
    assert h.record_failure("a", "boom 2") == 2
    row = h.all()["a"]
    assert row.consecutive_failures == 2 and row.last_error == "boom 2"
    assert row.last_ok_at is not None and row.last_signal_count == 7  # kept from the last success
    h.record_ok("a", 0)
    row = h.all()["a"]
    assert (row.consecutive_failures, row.last_error, row.last_signal_count) == (0, None, 0)


def test_failure_on_a_fresh_source(tmp_path):
    h = HealthStore(tmp_path / "e.db")
    assert h.record_failure("new", "x") == 1
    row = h.all()["new"]
    assert row.last_ok_at is None and row.last_signal_count is None


def test_health_survives_reopening(tmp_path):
    HealthStore(tmp_path / "e.db").record_failure("a", "x")
    assert HealthStore(tmp_path / "e.db").all()["a"].consecutive_failures == 1


class Slow:
    name = "slow"

    async def fetch(self, ctx, since):
        await asyncio.sleep(5)
        return []


class Fine:
    name = "fine"

    async def fetch(self, ctx, since):
        return []


class Boom:
    name = "boom"

    async def fetch(self, ctx, since):
        raise FetchError("down", hint="retry later")


async def test_timeout_becomes_fetch_error_with_hint(ctx):
    ctx.config.radar.source_timeout_s = 0.05
    with pytest.raises(FetchError, match=r"slow: timed out after 0\.05s") as ei:
        await fetch_with_timeout(Slow(), ctx, T0)
    assert ei.value.hint


async def test_one_slow_source_does_not_affect_the_others(ctx):
    ctx.config.radar.source_timeout_s = 0.05
    res = await run_radar([Slow(), Fine(), Boom()], ctx, T0)
    assert {e.adapter: e.message for e in res.errors}["slow"].startswith("slow: timed out")
    assert {e.adapter for e in res.errors} == {"slow", "boom"}


async def test_run_records_health_for_every_source(ctx):
    ctx.config.radar.source_timeout_s = 0.05
    await run_radar([Slow(), Fine(), Boom()], ctx, T0)
    await run_radar([Boom()], ctx, T0)
    rows = HealthStore(ctx.home.db_path).all()
    assert rows["fine"].consecutive_failures == 0 and rows["fine"].last_signal_count == 0
    assert rows["boom"].consecutive_failures == 2 and rows["boom"].last_error == "down"
    assert rows["slow"].consecutive_failures == 1 and "timed out" in rows["slow"].last_error


async def test_health_errors_never_break_a_run(ctx, monkeypatch):
    def explode(self, *a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(HealthStore, "record_ok", explode)
    monkeypatch.setattr(HealthStore, "record_failure", explode)
    res = await run_radar([Fine(), Boom()], ctx, T0)
    assert [e.adapter for e in res.errors] == ["boom"]


async def test_health_error_text_is_redacted(ctx):
    class Leaky:
        name = "leaky"

        async def fetch(self, ctx, since):
            raise RuntimeError("failed with fake-tavily")

    await run_radar([Leaky()], ctx, T0)
    assert "fake-tavily" not in HealthStore(ctx.home.db_path).all()["leaky"].last_error


async def test_full_default_source_set_with_mixed_failures(respx_mock, ctx, tmp_home):
    import httpx

    mock_sources(respx_mock, ctx.home)
    respx_mock.get("https://nation.africa/kenya/rss.xml").respond(404)
    respx_mock.get("https://www.centralbank.go.ke/news/").respond(200, text="<html>changed</html>")
    respx_mock.get("https://techcabal.com/feed/").mock(side_effect=httpx.ConnectError("refused"))
    tmp_home.sources_path.write_text("typo:\n  type: rss\n  kind: bad\n")
    res = await run_radar(
        build_adapters(ctx.config, ctx.home), ctx, datetime.now(UTC) - timedelta(days=2)
    )
    failed = {e.adapter for e in res.errors}
    assert failed == {"nation", "cbk_news", "techcabal", "typo"}
    assert len(res.signals) > 30
