"""Rubric scoring by the LLM; the final score and ranking are computed in code."""

from datetime import date

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from kenya_data_engine.context import RunContext
from kenya_data_engine.llm import load_prompt, run_agent
from kenya_data_engine.models import Signal, Topic, TopicScores, signal_id
from kenya_data_engine.research.memory import TopicMemory, code_novelty
from kenya_data_engine.synth.cluster import Cluster, format_signals


class ScoreOutput(BaseModel):
    data_ability: int = Field(ge=1, le=5)
    wallet_impact: int = Field(ge=1, le=5)
    timeliness: int = Field(ge=1, le=5)
    clarity_gap: int = Field(ge=1, le=5)
    novelty: int = Field(ge=1, le=5)
    justification: dict[str, str]
    why_now: str


def weighted_score(scores: TopicScores, weights: dict[str, float]) -> float:
    total: float = sum(weights[k] * getattr(scores, k) for k in weights)
    return round(total, 3)


def rank_topics(topics: list[Topic], top_n: int) -> list[Topic]:
    ordered = sorted(topics, key=lambda t: (-t.final_score, -len(t.signal_ids), t.title))
    return ordered[:top_n]


async def score_cluster(
    cluster: Cluster,
    signals: dict[str, Signal],
    ctx: RunContext,
    *,
    model: Model | None = None,
    topic_memory: TopicMemory | None = None,
    today: date | None = None,
) -> Topic:
    lines = format_signals([signals[i] for i in cluster.signal_ids if i in signals])
    prompt = (
        f"Topic: {cluster.title}\nSummary: {cluster.summary}\n"
        f"Category: {cluster.category}\nSignals:\n{lines}"
    )
    agent: Agent[None, ScoreOutput] = Agent(
        output_type=ScoreOutput, instructions=load_prompt("score")
    )
    tid = signal_id(None, cluster.title + "|" + ",".join(sorted(cluster.signal_ids)))
    out = await run_agent(
        agent, prompt, ctx, stage="synthesize_score", name="score", topic_id=tid, model=model
    )
    novelty, justification = out.novelty, dict(out.justification)
    if topic_memory is not None and today is not None:
        days = topic_memory.last_covered(cluster.title, today)
        capped = code_novelty(days, ctx.config.research.novelty_days)
        if capped is not None:  # recency is a fact we hold; the LLM cannot score it away
            novelty = min(novelty, capped)
            note = f" (covered {days} days ago)"
            justification["novelty"] = justification.get("novelty", "").rstrip() + note
    scores = TopicScores(
        data_ability=out.data_ability,
        wallet_impact=out.wallet_impact,
        timeliness=out.timeliness,
        clarity_gap=out.clarity_gap,
        novelty=novelty,
        justification=justification,
    )
    return Topic(
        id=tid,
        title=cluster.title,
        summary=cluster.summary,
        why_now=out.why_now,
        category=cluster.category,
        signal_ids=cluster.signal_ids,
        scores=scores,
        final_score=weighted_score(scores, ctx.config.weights),
    )
