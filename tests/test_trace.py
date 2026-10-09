import json

import pytest

from kenya_data_engine.errors import BudgetExceeded
from kenya_data_engine.trace import Tracer


def make(tmp_path, budget=0.50):
    return Tracer(tmp_path / "t.jsonl", "r", 0.15, 0.60, budget)


def lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / "t.jsonl").read_text().splitlines()]


def test_llm_cost_uses_pricing(tmp_path):
    t = make(tmp_path)
    assert t.record_llm("synthesize", "cluster", 1_000_000, 1_000_000, 10) == pytest.approx(0.75)
    assert t.total_cost == pytest.approx(0.75)


def test_budget_exceeded(tmp_path):
    t = make(tmp_path, 0.10)
    t.record_llm("s", "n", 1_000_000, 0, 1)
    assert t.remaining_usd == pytest.approx(-0.05)
    with pytest.raises(BudgetExceeded) as ei:
        t.check_budget()
    assert "budgets.run_usd" in (ei.value.hint or "")


def test_budget_ok_below_limit(tmp_path):
    t = make(tmp_path)
    t.record_llm("s", "n", 1000, 1000, 1)
    t.check_budget()
    assert t.remaining_usd < 0.50


async def test_span_records_ok(tmp_path):
    t = make(tmp_path)
    async with t.span("radar", "tool", "x", topic_id="t1"):
        pass
    line = lines(tmp_path)[-1]
    assert line["status"] == "ok" and line["topic_id"] == "t1" and line["run_id"] == "r"


async def test_span_records_error_and_reraises(tmp_path):
    t = make(tmp_path)
    with pytest.raises(ValueError):
        async with t.span("radar", "tool", "x"):
            raise ValueError("boom")
    line = lines(tmp_path)[-1]
    assert line["status"] == "error" and line["error"] == "boom"


def test_no_secret_in_trace(tmp_path):
    from datetime import UTC, datetime

    from kenya_data_engine.trace import TraceEvent

    t = make(tmp_path)
    t.record(
        TraceEvent(
            ts=datetime.now(UTC),
            run_id="r",
            stage="s",
            kind="tool",
            name="n",
            status="ok",
            latency_ms=1,
            attrs={"api_key": "sk-123", "nested": {"Auth_Token": "x", "ok": 1}, "n": "fine"},
        )
    )
    raw = (tmp_path / "t.jsonl").read_text()
    assert "sk-123" not in raw and '"x"' not in raw
    attrs = json.loads(raw)["attrs"]
    assert attrs["api_key"] == "***" and attrs["nested"]["Auth_Token"] == "***"
    assert attrs["n"] == "fine" and attrs["nested"]["ok"] == 1


def test_summary(tmp_path):
    t = make(tmp_path)
    t.record_llm("synthesize", "a", 1_000_000, 0, 1)
    t.record_llm("synthesize", "b", 0, 1_000_000, 1, status="error", error="x")
    t.record_llm("radar", "c", 0, 0, 1)
    s = t.summary()
    assert s["errors"] == 1
    assert s["stages"]["synthesize"]["events"] == 2
    assert s["stages"]["synthesize"]["cost_usd"] == pytest.approx(0.75)
    assert s["stages"]["radar"]["events"] == 1
    assert s["total_cost_usd"] == pytest.approx(0.75)


def test_secret_values_redacted_in_error_and_attrs(tmp_path):
    from datetime import UTC, datetime

    from kenya_data_engine.trace import TraceEvent

    t = Tracer(tmp_path / "t.jsonl", "r", 0.15, 0.60, 0.5, secrets=["sk-abc123"])
    t.record(
        TraceEvent(
            ts=datetime.now(UTC),
            run_id="r",
            stage="s",
            kind="http",
            name="n",
            status="error",
            latency_ms=1,
            error="401 for https://x?k=sk-abc123",
            attrs={"url": "https://x?k=sk-abc123", "nested": ["sk-abc123"], "n": 1},
        )
    )
    raw = (tmp_path / "t.jsonl").read_text()
    assert "sk-abc123" not in raw
    row = lines(tmp_path)[0]
    assert row["error"] == "401 for https://x?k=***"
    assert row["attrs"]["n"] == 1


def test_tracer_seeds_from_existing_trace(tmp_path):
    first = make(tmp_path, 1.0)
    first.record_llm("synthesize_score", "t", 1_000_000, 500_000, 5)  # $0.45
    resumed = make(tmp_path, 0.50)
    assert resumed.total_cost == pytest.approx(0.45)
    assert resumed.summary()["stages"]["synthesize_score"]["events"] == 1
    resumed.record_llm("synthesize_score", "t", 400_000, 0, 5)  # $0.06
    assert resumed.summary()["events"] == 2
    with pytest.raises(BudgetExceeded):
        resumed.check_budget()


def test_tracer_ignores_garbage_trace_lines(tmp_path):
    (tmp_path / "t.jsonl").write_text('{"truncated\nnot json\n')
    assert make(tmp_path).total_cost == 0.0
