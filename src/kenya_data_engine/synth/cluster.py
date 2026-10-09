"""Group signals into candidate topics with one structured LLM call."""

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model

from kenya_data_engine.context import RunContext
from kenya_data_engine.llm import load_prompt, run_agent
from kenya_data_engine.models import Category, Signal


class Cluster(BaseModel):
    title: str
    summary: str
    category: Category
    signal_ids: list[str]


class ClusterOutput(BaseModel):
    clusters: list[Cluster]


def label_for(index: int) -> str:
    """Short prompt label for the signal at `index` (0-based): S1, S2, ..."""
    return f"S{index + 1}"


def format_signals(signals: list[Signal]) -> str:
    """One line per signal, led by a short S-label (models mis-copy 12-hex ids)."""
    return "\n".join(
        f"{label_for(i)} | {s.kind} | {s.source} | "
        f"{s.published_at.strftime('%Y-%m-%d') if s.published_at else ''} | {s.title[:120]}"
        for i, s in enumerate(signals)
    )


def _truncate(signals: list[Signal], limit: int) -> list[Signal]:
    """Keep every calendar signal, then the most recent others, up to `limit`."""
    if len(signals) <= limit:
        return signals
    calendar = [s for s in signals if s.kind == "calendar"]
    others = sorted(
        (s for s in signals if s.kind != "calendar"),
        key=lambda s: s.published_at.timestamp() if s.published_at else 0.0,
        reverse=True,
    )
    keep = {s.id for s in others[: max(limit - len(calendar), 0)]}
    keep.update(s.id for s in calendar)
    return [s for s in signals if s.id in keep]


async def cluster_signals(
    signals: list[Signal], ctx: RunContext, *, model: Model | None = None
) -> tuple[list[Cluster], list[str]]:
    if not signals:
        return [], []
    signals = _truncate(signals, ctx.config.synth.max_signals)
    by_label = {label_for(i): s.id for i, s in enumerate(signals)}
    agent: Agent[None, ClusterOutput] = Agent(
        output_type=ClusterOutput, instructions=load_prompt("cluster")
    )
    out = await run_agent(
        agent,
        "Signals:\n" + format_signals(signals),
        ctx,
        stage="synthesize_cluster",
        name="cluster",
        model=model,
    )
    clusters: list[Cluster] = []
    dropped: list[str] = []
    used: set[str] = set()
    for c in out.clusters:
        ids = []
        for label in c.signal_ids:
            sid = by_label.get(label.strip().upper())
            if sid is not None and sid not in used:
                used.add(sid)
                ids.append(sid)
        if ids:
            clusters.append(c.model_copy(update={"signal_ids": ids}))
        else:
            dropped.append(f"{c.title}: no valid signals")
    return clusters, dropped
