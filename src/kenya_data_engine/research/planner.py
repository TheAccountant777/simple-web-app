"""Planner agent: topic in, ResearchBrief out. Ids and labels are normalised in code."""

import re
from datetime import date

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from kenya_data_engine.llm import load_prompt, run_agent
from kenya_data_engine.research.models import ResearchBrief
from kenya_data_engine.research.tools import (
    ResearchDeps,
    memory_lookup,
    read_page,
    registry_lookup,
    web_search,
)

STAGE = "research_planner"
LIMITS = UsageLimits(request_limit=10, tool_calls_limit=8)


class TopicInput(BaseModel):
    title: str
    summary: str
    why_now: str = ""
    signals: list[str] = []  # "title — url"


def planner_instructions(today: date) -> str:
    return load_prompt("planner").replace("{{today}}", today.isoformat())


def build_planner() -> Agent[ResearchDeps, ResearchBrief]:
    return Agent(
        deps_type=ResearchDeps,
        output_type=ResearchBrief,
        tools=[web_search, read_page, registry_lookup, memory_lookup],
    )


def _escape(text: str) -> str:
    return re.sub(r"<(\s*/\s*topic)", r"<\\\1", text, flags=re.IGNORECASE)


def topic_prompt(topic: TopicInput) -> str:
    lines = [f"Topic: {topic.title}", f"Summary: {topic.summary}"]
    if topic.why_now:
        lines.append(f"Why now: {topic.why_now}")
    if topic.signals:
        lines.append("Signals:\n" + "\n".join(f"- {s}" for s in topic.signals))
    return '<topic untrusted="true">\n' + _escape("\n".join(lines)) + "\n</topic>"


def _normalise(brief: ResearchBrief) -> ResearchBrief:
    """Code owns ids and cross-references; the model's are only hints."""
    for i, need in enumerate(brief.data_needs, start=1):
        need.id = f"n{i}"
        if need.period_start and need.period_end and need.period_start > need.period_end:
            need.period_start, need.period_end = need.period_end, need.period_start
    need_ids = {n.id for n in brief.data_needs}
    concept_ids: dict[str, str] = {}
    for i, concept in enumerate(brief.chart_concepts, start=1):
        concept_ids.setdefault(concept.id.strip().lower(), f"c{i}")
        concept_ids.setdefault(f"c{i}", f"c{i}")
        concept.id = f"c{i}"
    for concept in brief.chart_concepts:
        kept = [n.strip().lower() for n in concept.needs]
        concept.needs = list(dict.fromkeys(n for n in kept if n in need_ids))
    for need in brief.data_needs:
        mapped = (concept_ids.get(c.strip().lower()) for c in need.chart_concepts)
        need.chart_concepts = list(dict.fromkeys(c for c in mapped if c))
    return brief


async def plan(
    topic: TopicInput, deps: ResearchDeps, *, today: date, model: Model | None = None
) -> ResearchBrief:
    agent = build_planner()
    agent.instructions(lambda: planner_instructions(today))
    brief = await run_agent(
        agent,
        topic_prompt(topic),
        deps.ctx,
        stage=STAGE,
        name="planner",
        deps=deps,
        usage_limits=LIMITS,
        ledger=deps.ledger,
        group="planner",
        model=model,
    )
    return _normalise(brief)
