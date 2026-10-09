from datetime import UTC, datetime

import pytest

from kenya_data_engine.errors import EngineError
from kenya_data_engine.models import RadarResult, Signal
from kenya_data_engine.runs import RunStore

NOW = datetime(2026, 10, 9, 14, 5)


def test_run_id_format_and_collision(tmp_path):
    s = RunStore(tmp_path)
    assert s.new_run(NOW).run_id == "2026-10-09-1405"
    assert s.new_run(NOW).run_id == "2026-10-09-1405-2"
    assert s.new_run(NOW).run_id == "2026-10-09-1405-3"


def test_read_returns_none_for_truncated_json(tmp_path):  # Review Focus 5
    h = RunStore(tmp_path).new_run(NOW)
    (h.dir / "signals.json").write_text('{"signals": [')
    assert h.read("signals", RadarResult) is None


def test_read_returns_none_for_missing_and_invalid_schema(tmp_path):
    h = RunStore(tmp_path).new_run(NOW)
    assert h.read("signals", RadarResult) is None
    (h.dir / "signals.json").write_text('{"signals": "nope"}')
    assert h.read("signals", RadarResult) is None


def test_write_read_roundtrip(tmp_path):
    h = RunStore(tmp_path).new_run(NOW)
    sig = Signal(
        id="abc",
        kind="news",
        title="T",
        source="nation",
        url="https://x.com/a",
        published_at=datetime(2026, 10, 9, tzinfo=UTC),
        snippet="s",
        meta={"k": "v"},
    )
    rr = RadarResult(signals=[sig], errors=[], collected_at=datetime(2026, 10, 9, tzinfo=UTC))
    path = h.write("signals", rr)
    assert path == h.dir / "signals.json"
    assert not list(h.dir.glob("*.tmp"))
    assert h.read("signals", RadarResult) == rr


def test_trace_path(tmp_path):
    h = RunStore(tmp_path).new_run(NOW)
    assert h.trace_path == h.dir / "trace.jsonl"


def test_open_latest_and_list(tmp_path):
    s = RunStore(tmp_path)
    assert s.latest() is None and s.list() == []
    a = s.new_run(datetime(2026, 10, 9, 14, 5))
    b = s.new_run(datetime(2026, 10, 9, 14, 5))
    c = s.new_run(datetime(2026, 10, 10, 8, 0))
    assert s.list() == [c.run_id, b.run_id, a.run_id]
    latest = s.latest()
    assert latest is not None and latest.run_id == c.run_id
    assert s.open(a.run_id).dir == a.dir


def test_open_missing_raises_engine_error(tmp_path):
    with pytest.raises(EngineError):
        RunStore(tmp_path).open("nope")


def test_open_missing_run_hint_points_to_runs_dir(tmp_path):
    with pytest.raises(EngineError) as e:
        RunStore(tmp_path).open("2026-10-09-1405")
    assert "engine runs" not in (e.value.hint or "") and str(tmp_path) in (e.value.hint or "")


@pytest.mark.parametrize("bad", ["../x", "x", "2026-10-09", "", "/etc", "2026-10-09-1405/../.."])
def test_open_rejects_invalid_run_id(tmp_path, bad):
    (tmp_path.parent / "x").mkdir(exist_ok=True)
    with pytest.raises(EngineError) as e:
        RunStore(tmp_path).open(bad)
    assert "invalid run id" in e.value.message and "YYYY-MM-DD-HHMM" in (e.value.hint or "")
