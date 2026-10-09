"""Writes synthetic run folders for report/TUI tests."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kenya_data_engine.runs import RunHandle


def ev(kind, stage, name, *, ts, latency=100, status="ok", cost=0.0, **extra):
    attrs = extra.pop("attrs", {})
    return {
        "ts": ts.isoformat(),
        "run_id": "r",
        "stage": stage,
        "kind": kind,
        "name": name,
        "status": status,
        "latency_ms": latency,
        "topic_id": None,
        "input_tokens": extra.get("input_tokens", 0),
        "output_tokens": extra.get("output_tokens", 0),
        "cost_usd": cost,
        "error": extra.get("error"),
        "attrs": attrs,
    }


TOPICS = {
    "topics": [
        {
            "id": "t1",
            "title": "Fuel prices",
            "summary": "s",
            "why_now": "w",
            "category": "economy",
            "signal_ids": ["a"],
            "scores": {
                "data_ability": 4,
                "wallet_impact": 4,
                "timeliness": 4,
                "clarity_gap": 4,
                "novelty": 4,
                "justification": {},
            },
            "final_score": 4.0,
        }
    ],
    "dropped": ["x"],
}


def make_run(
    runs_dir: Path,
    run_id: str = "2026-10-09-0800",
    *,
    complete: bool = True,
    cache_hits: int = 1,
    bad_line: bool = False,
    radar_ms: int = 400,
    kenya_ok: bool = True,
) -> RunHandle:
    path = runs_dir / run_id
    path.mkdir(parents=True)
    t0 = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    events = [
        ev("http", "http", "a.ke", ts=t0, latency=50, attrs={"from_cache": i < cache_hits})
        for i in range(2)
    ]
    events += [
        ev("tool", "radar", "kenya_news", ts=t0, latency=300, attrs={"signals": 7})
        if kenya_ok
        else ev("tool", "radar", "kenya_news", ts=t0, latency=900, status="error", error="timeout"),
        ev("tool", "radar", "cbk", ts=t0, latency=200, attrs={"signals": 3}),
        ev("stage", "radar", "radar", ts=t0, latency=radar_ms),
        ev(
            "llm",
            "synthesize",
            "cluster",
            ts=t0,
            latency=1000,
            cost=0.02,
            input_tokens=1000,
            output_tokens=200,
        ),
        ev(
            "llm",
            "synthesize",
            "score",
            ts=t0,
            latency=3000,
            cost=0.03,
            input_tokens=2000,
            output_tokens=400,
            status="error",
            error="bad",
        ),
    ]
    if complete:
        events.append(
            ev("stage", "synthesize", "synthesize", ts=t0 + timedelta(seconds=5), latency=4000)
        )
        (path / "topics.json").write_text(json.dumps(TOPICS), encoding="utf-8")
    lines = [json.dumps(e) for e in events]
    if bad_line:
        lines.insert(1, "{not json")
    (path / "trace.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return RunHandle(run_id, path)
