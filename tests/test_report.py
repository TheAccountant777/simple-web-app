import json

import pytest
from runs_factory import make_run

from kenya_data_engine.report import RunMetrics, build_report, load_run_metrics, percentile


def test_metrics_from_complete_run(tmp_path):
    m = load_run_metrics(make_run(tmp_path), 0.5)
    assert m.complete and m.topics == 1 and m.dropped == 1 and m.budget_usd == 0.5
    assert m.cost_usd == pytest.approx(0.05)
    assert [s.name for s in m.stages] == ["radar", "synthesize"]
    assert m.stages[0].detail == "10 signals · 0 sources failed"
    assert m.stages[1].detail == "1 topics · 1 dropped"
    assert {s.name: s.signals for s in m.sources} == {"kenya_news": 7, "cbk": 3}
    assert m.llm.calls == 2 and m.llm.errors == 1 and m.llm.mean_latency_ms == 2000
    assert (m.llm.input_tokens, m.llm.output_tokens) == (3000, 600)
    assert m.errors == 1 and m.duration_ms >= 5000 and m.started_at is not None


def test_incomplete_run_flagged(tmp_path):
    m = load_run_metrics(make_run(tmp_path, complete=False, bad_line=True), 0.5)
    assert not m.complete and m.topics == 0 and len(m.stages) == 1
    # a run with no trace at all still loads
    empty = tmp_path / "2026-10-09-0900"
    empty.mkdir()
    from kenya_data_engine.runs import RunHandle

    e = load_run_metrics(RunHandle(empty.name, empty), 0.5)
    assert not e.complete and e.duration_ms == 0 and e.started_at is None


def test_cache_hit_rate(tmp_path):
    m = load_run_metrics(make_run(tmp_path, cache_hits=1), 0.5)
    assert (m.http_requests, m.cache_hits, m.cache_hit_rate) == (2, 1, 0.5)
    none = load_run_metrics(make_run(tmp_path, "2026-10-09-0900", cache_hits=0), 0.5)
    assert none.cache_hit_rate == 0.0


def test_percentiles_nearest_rank():
    assert percentile([], 50) == 0
    vals = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert percentile(vals, 50) == 50 and percentile(vals, 95) == 100
    assert percentile([5], 95) == 5 and percentile(vals, 0) == 10


def test_source_success_rate_across_runs(tmp_path):
    runs = [
        load_run_metrics(make_run(tmp_path, "2026-10-09-0800", radar_ms=100), 0.5),
        load_run_metrics(make_run(tmp_path, "2026-10-09-0900", radar_ms=300, kenya_ok=False), 0.5),
    ]
    agg = build_report(runs)
    assert agg.runs == 2
    assert agg.source_success_rate == {"kenya_news": 0.5, "cbk": 1.0}
    assert agg.source_mean_latency_ms["kenya_news"] == 600
    assert agg.source_last_error == {"kenya_news": "timeout", "cbk": None}
    assert agg.stage_p50_ms["radar"] == 100 and agg.stage_p95_ms["radar"] == 300
    assert [r for r, _ in agg.cost_per_run] == ["2026-10-09-0800", "2026-10-09-0900"]
    assert agg.mean_cache_hit_rate == 0.5
    assert build_report([]).mean_cache_hit_rate is None


def test_run_metrics_serialises(tmp_path):
    m = load_run_metrics(make_run(tmp_path), 0.5)
    assert RunMetrics.model_validate(json.loads(m.model_dump_json())) == m


def test_invalid_utf8_trace_is_tolerated(tmp_path):
    run = make_run(tmp_path)
    good = run.trace_path.read_bytes()
    run.trace_path.write_bytes(b"\xff\xfe\x80 junk\n" + good)
    m = load_run_metrics(run, 0.5)
    assert m.complete and m.llm.calls == 2


def test_cached_attempts_are_excluded_from_live_latency(tmp_path):
    runs = [
        load_run_metrics(make_run(tmp_path, "2026-10-09-0800"), 0.5),  # kenya live 300ms
        load_run_metrics(make_run(tmp_path, "2026-10-09-0900", kenya_cached=True), 0.5),  # 5ms
    ]
    src = runs[1].sources[0]
    assert (src.http, src.cache_hits, src.cached) == (2, 2, True)
    agg = build_report(runs)
    assert agg.source_mean_latency_ms["kenya_news"] == 300  # the 5ms cache replay is ignored
    assert agg.source_cache_share["kenya_news"] == 0.5  # 2 of 4 http calls
    assert agg.source_cache_share["cbk"] == 0.5
    assert agg.source_success_rate["kenya_news"] == 1.0  # cached runs still count as successes


def test_source_whose_calls_were_all_cached_has_no_live_latency(tmp_path):
    agg = build_report([load_run_metrics(make_run(tmp_path, kenya_cached=True), 0.5)])
    assert agg.source_mean_latency_ms["kenya_news"] is None
    assert agg.source_cache_share["kenya_news"] == 1.0
    assert agg.source_mean_latency_ms["cbk"] == 200


def test_old_traces_without_http_attrs_count_as_live(tmp_path):
    run = make_run(tmp_path)
    lines = [json.loads(x) for x in run.trace_path.read_text().splitlines()]
    for e in lines:
        e["attrs"].pop("http", None)
        e["attrs"].pop("cache_hits", None)
    run.trace_path.write_text("\n".join(json.dumps(e) for e in lines) + "\n")
    agg = build_report([load_run_metrics(run, 0.5)])
    assert agg.source_mean_latency_ms["kenya_news"] == 300
    assert agg.source_cache_share["kenya_news"] is None


def test_only_configured_sources_are_reported(tmp_path):
    runs = [load_run_metrics(make_run(tmp_path), 0.5)]
    agg = build_report(runs, only={"kenya_news"})
    assert set(agg.source_success_rate) == {"kenya_news"}
    assert set(agg.source_mean_latency_ms) == {"kenya_news"}
    assert set(build_report(runs).source_success_rate) == {"kenya_news", "cbk"}


def test_enabled_source_names_follow_sources_yaml(tmp_home):
    from kenya_data_engine.report import enabled_source_names

    tmp_home.sources_path.write_text("nation:\n  enabled: false\n", encoding="utf-8")
    names = enabled_source_names(tmp_home)
    assert "calendar" in names and "standard" in names and "nation" not in names
