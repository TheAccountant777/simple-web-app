import json
from datetime import UTC, datetime

import httpx
import pytest

from kenya_data_engine.cache import Cache
from kenya_data_engine.http import fetch
from kenya_data_engine.radar.base import run_radar
from kenya_data_engine.trace import TraceEvent, Tracer

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def make(tmp_path, secrets=()):
    return Tracer(tmp_path / "t.jsonl", "r", 0.15, 0.60, 1.0, secrets=secrets)


def ev(**kw):
    base = dict(ts=T0, run_id="r", stage="s", kind="tool", name="n", status="ok", latency_ms=1)
    return TraceEvent(**{**base, **kw})


def test_subscriber_receives_redacted_events(tmp_path):
    t = make(tmp_path, secrets=["hunter2"])
    seen, order = [], []
    unsub = t.subscribe(seen.append)
    t.subscribe(lambda e: order.append("second"))
    t.record(ev(error="bad hunter2", attrs={"url": "x", "api_key": "hunter2"}))
    assert seen[0].error == "bad ***" and seen[0].attrs["api_key"] == "***"
    assert order == ["second"]
    unsub()
    unsub()  # idempotent
    t.record(ev())
    assert len(seen) == 1


def test_raising_subscriber_is_dropped_not_fatal(tmp_path):
    t = make(tmp_path)
    calls = []

    def bad(e):
        calls.append(1)
        raise RuntimeError("boom")

    t.subscribe(bad)
    t.record(ev())
    t.record(ev())
    assert calls == [1]
    assert len((tmp_path / "t.jsonl").read_text().splitlines()) == 2


async def test_span_attrs_merged(tmp_path):
    t = make(tmp_path)
    async with t.span("radar", "tool", "x") as attrs:
        attrs["signals"] = 4
    async with t.span("radar", "tool", "y"):
        pass
    rows = [json.loads(x) for x in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert rows[0]["attrs"] == {"signals": 4} and rows[1]["attrs"] == {}


async def test_fetch_records_http_event_with_cache_flag(tmp_path, respx_mock):
    respx_mock.get("https://a.ke/x?token=abc").respond(200, text="hello")
    t = make(tmp_path, secrets=["abc"])
    events = []
    t.subscribe(events.append)
    cache = Cache(tmp_path / "e.db")
    async with httpx.AsyncClient() as c:
        for _ in range(2):
            await fetch("https://a.ke/x?token=abc", client=c, cache=cache, ttl_hours=1, tracer=t)
    first, second = events
    assert first.kind == "http" and first.name == "a.ke" and first.status == "ok"
    assert first.attrs["status"] == 200 and first.attrs["bytes"] == 5
    assert first.attrs["from_cache"] is False and first.attrs["attempts"] == 1
    assert "abc" not in first.attrs["url"]
    assert second.attrs["from_cache"] is True and second.attrs["attempts"] == 0


async def test_fetch_failure_records_error_event(tmp_path, respx_mock):
    from kenya_data_engine.errors import FetchError

    respx_mock.get("https://a.ke/x").respond(404)
    t = make(tmp_path)
    events = []
    t.subscribe(events.append)
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError):
            await fetch(
                "https://a.ke/x", client=c, cache=Cache(tmp_path / "e.db"), ttl_hours=1, tracer=t
            )
    assert events[0].status == "error" and events[0].attrs["status"] == 404


async def test_radar_span_records_signal_count(ctx):
    class A:
        name = "a"

        async def fetch(self, ctx, since):
            return []

    events = []
    ctx.tracer.subscribe(events.append)
    await run_radar([A()], ctx, since=T0)
    assert events[0].attrs["signals"] == 0


def test_subscriber_that_unsubscribes_then_raises_is_safe(tmp_path):
    tracer = Tracer(tmp_path / "t.jsonl", "r", 1.0, 1.0, 1.0)
    holder = []

    def bad(event):
        holder[0]()
        raise RuntimeError("boom")

    holder.append(tracer.subscribe(bad))
    tracer.record_llm("s", "n", 1, 1, 1)  # must not raise ValueError from list.remove
    tracer.record_llm("s", "n", 1, 1, 1)
