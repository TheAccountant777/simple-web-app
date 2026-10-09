"""Helpers for Textual pilot tests: plain-text screen dumps and synthetic run content."""

import json
from datetime import UTC, datetime

from kenya_data_engine.models import RadarResult, Signal


def screen_text(app) -> str:
    """The visible screen as plain text (what a person would read)."""
    strips = app.screen._compositor.render_strips()
    return "\n".join("".join(seg.text for seg in strip).rstrip() for strip in strips)


def add_signals(run, ids=("a",), url="https://example.ke/story"):
    signals = [
        Signal(
            id=i,
            kind="news",
            title=f"Fuel pump prices rise again {i}",
            source="kenya_news",
            url=url,
            published_at=datetime(2026, 10, 8, tzinfo=UTC),
        )
        for i in ids
    ]
    run.write("signals", RadarResult(signals=signals, errors=[], collected_at=datetime.now(UTC)))


def add_exchange(run, seq=1, *, name="score", topic_id="t1", stage="synthesize_score", **over):
    (run.dir / "llm").mkdir(exist_ok=True)
    payload = {
        "seq": seq,
        "stage": stage,
        "name": name,
        "topic_id": topic_id,
        "model": "deepseek-chat",
        "settings": {"max_tokens": 4096, "extra_body": {}},
        "messages": [
            {
                "kind": "request",
                "instructions": "You are a careful editor.",
                "parts": [{"part_kind": "user-prompt", "content": "Score the fuel topic"}],
            },
            {
                "kind": "response",
                "parts": [
                    {
                        "part_kind": "tool-call",
                        "tool_name": "final_result",
                        "args": {"data_ability": 4},
                    }
                ],
            },
        ],
        "output": {"data_ability": 4},
        "usage": {"input_tokens": 1200, "output_tokens": 300},
        "latency_ms": 1500,
        "cost_usd": 0.0012,
        "status": "ok",
        "error": None,
        **over,
    }
    path = run.dir / "llm" / f"{seq:04d}-{stage}-{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
