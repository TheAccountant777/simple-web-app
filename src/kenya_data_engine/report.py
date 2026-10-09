"""Pure run-metrics model: turns run folders into numbers (no I/O beyond reading the run)."""

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from kenya_data_engine.models import TopicList
from kenya_data_engine.runs import RunHandle
from kenya_data_engine.trace import TraceEvent


class SourceStat(BaseModel):
    name: str
    status: Literal["ok", "error"]
    latency_ms: int
    signals: int | None = None
    error: str | None = None


class StageStat(BaseModel):
    name: str
    status: Literal["ok", "error"]
    duration_ms: int
    detail: str = ""


class LlmStat(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    mean_latency_ms: int = 0
    errors: int = 0


class RunMetrics(BaseModel):
    run_id: str
    started_at: datetime | None = None
    duration_ms: int = 0
    complete: bool = False
    cost_usd: float = 0.0
    budget_usd: float = 0.0
    stages: list[StageStat] = Field(default_factory=list)
    sources: list[SourceStat] = Field(default_factory=list)
    llm: LlmStat = Field(default_factory=LlmStat)
    http_requests: int = 0
    cache_hits: int = 0
    cache_hit_rate: float | None = None
    topics: int = 0
    dropped: int = 0
    errors: int = 0


class Aggregate(BaseModel):
    runs: int
    stage_p50_ms: dict[str, int]
    stage_p95_ms: dict[str, int]
    source_success_rate: dict[str, float]
    source_mean_latency_ms: dict[str, int]
    cost_per_run: list[tuple[str, float]]
    duration_per_run: list[tuple[str, int]]
    mean_cache_hit_rate: float | None


def _read_events(run: RunHandle) -> list[TraceEvent]:
    try:
        lines = run.trace_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return []
    events: list[TraceEvent] = []
    for line in lines:
        try:
            events.append(TraceEvent.model_validate_json(line))
        except ValueError:
            continue  # a torn or foreign line must not hide the rest of the run
    return events


def _stage_detail(name: str, sources: list[SourceStat], topics: TopicList | None) -> str:
    if name == "radar" and sources:
        found = sum(s.signals or 0 for s in sources)
        failed = sum(s.status == "error" for s in sources)
        return f"{found} signals · {failed} sources failed"
    if name == "synthesize" and topics is not None:
        return f"{len(topics.topics)} topics · {len(topics.dropped)} dropped"
    return ""


def load_run_metrics(run: RunHandle, budget_usd: float) -> RunMetrics:
    events = _read_events(run)
    topics = run.read("topics", TopicList)
    sources = [
        SourceStat(
            name=e.name,
            status=e.status,
            latency_ms=e.latency_ms,
            signals=e.attrs.get("signals") if isinstance(e.attrs.get("signals"), int) else None,
            error=e.error,
        )
        for e in events
        if e.kind == "tool" and e.stage == "radar"
    ]
    stages = [
        StageStat(
            name=e.name,
            status=e.status,
            duration_ms=e.latency_ms,
            detail=_stage_detail(e.name, sources, topics),
        )
        for e in events
        if e.kind == "stage"
    ]
    llm_events = [e for e in events if e.kind == "llm"]
    http = [e for e in events if e.kind == "http"]
    hits = sum(bool(e.attrs.get("from_cache")) for e in http)
    started = duration = None
    if events:
        started = min(e.ts - timedelta(milliseconds=e.latency_ms) for e in events)
        duration = int((max(e.ts for e in events) - started).total_seconds() * 1000)
    return RunMetrics(
        run_id=run.run_id,
        started_at=started,
        duration_ms=duration or 0,
        # Finished = the last stage reported ok and the final artifact was written.
        complete=bool(stages) and stages[-1].status == "ok" and topics is not None,
        cost_usd=sum(e.cost_usd for e in events),
        budget_usd=budget_usd,
        stages=stages,
        sources=sources,
        llm=LlmStat(
            calls=len(llm_events),
            input_tokens=sum(e.input_tokens for e in llm_events),
            output_tokens=sum(e.output_tokens for e in llm_events),
            cost_usd=sum(e.cost_usd for e in llm_events),
            mean_latency_ms=(
                int(sum(e.latency_ms for e in llm_events) / len(llm_events)) if llm_events else 0
            ),
            errors=sum(e.status == "error" for e in llm_events),
        ),
        http_requests=len(http),
        cache_hits=hits,
        cache_hit_rate=hits / len(http) if http else None,
        topics=len(topics.topics) if topics else 0,
        dropped=len(topics.dropped) if topics else 0,
        errors=sum(e.status == "error" for e in events),
    )


def percentile(values: list[int], pct: float) -> int:
    """Nearest-rank percentile (0 for no data)."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(math.ceil(pct / 100 * len(ordered)) - 1, 0)]


def build_report(runs: list[RunMetrics]) -> Aggregate:
    stage_ms: dict[str, list[int]] = defaultdict(list)
    attempts: dict[str, list[SourceStat]] = defaultdict(list)
    for r in runs:
        for st in r.stages:
            stage_ms[st.name].append(st.duration_ms)
        for src in r.sources:
            attempts[src.name].append(src)
    rates = [r.cache_hit_rate for r in runs if r.cache_hit_rate is not None]
    return Aggregate(
        runs=len(runs),
        stage_p50_ms={k: percentile(v, 50) for k, v in stage_ms.items()},
        stage_p95_ms={k: percentile(v, 95) for k, v in stage_ms.items()},
        source_success_rate={
            k: sum(s.status == "ok" for s in v) / len(v) for k, v in attempts.items()
        },
        source_mean_latency_ms={
            k: int(sum(s.latency_ms for s in v) / len(v)) for k, v in attempts.items()
        },
        cost_per_run=[(r.run_id, r.cost_usd) for r in runs],
        duration_per_run=[(r.run_id, r.duration_ms) for r in runs],
        mean_cache_hit_rate=sum(rates) / len(rates) if rates else None,
    )
