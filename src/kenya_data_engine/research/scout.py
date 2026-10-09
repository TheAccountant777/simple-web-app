"""Scout agent: one data need in, source specs out. Code vets every spec the model returns."""

from datetime import date

from pydantic import BaseModel
from pydantic_ai import Agent, Tool
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from kenya_data_engine.data.periods import today_nairobi
from kenya_data_engine.errors import BudgetExceeded, EngineError
from kenya_data_engine.llm import load_prompt, run_agent
from kenya_data_engine.models import normalize_url
from kenya_data_engine.research.models import DataNeed, DataSourceSpec
from kenya_data_engine.research.tools import (
    Ctx,
    ResearchDeps,
    list_links,
    memory_lookup,
    preview_table,
    read_page,
    registry_lookup,
    url_allowed,
    web_search,
)

STAGE = "research_scout"
LIMITS = UsageLimits(request_limit=8, tool_calls_limit=10)
MAX_SPECS = 3


class ScoutOutput(BaseModel):
    specs: list[DataSourceSpec] = []


class ScoutResult(BaseModel):
    need_id: str
    specs: list[DataSourceSpec]
    rejected: list[str]
    registry_hit: bool = False
    memory_hit: bool = False
    llm_used: bool = False


def scout_instructions(today: date) -> str:
    return load_prompt("scout").replace("{{today}}", today.isoformat())


def need_prompt(need: DataNeed, hint: str) -> str:
    lines = [f"Need id: {need.id}", f"Kind: {need.kind}", f"Question: {need.question}"]
    for label, value in (
        ("Metric", need.metric),
        ("Entities", ", ".join(need.entities)),
        ("Unit", need.unit),
        ("Frequency", need.frequency if need.frequency != "none" else None),
        ("Period", f"{need.period_start} to {need.period_end}" if need.period_start else None),
        ("Preferred publishers", ", ".join(need.publishers)),
        ("Registry hint", need.series_hint),
        ("Hint from an earlier round", hint),
    ):
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def build_scout(need: DataNeed) -> Agent[ResearchDeps, ScoutOutput]:
    async def scout_search(ctx: Ctx, query: str, domains: list[str] | None = None) -> str:
        """Search the web. Returns numbered results: title, url, snippet."""
        # the need's preferred publishers apply whenever the model names no domains
        return await web_search(ctx, query, domains or need.publishers or None)

    return Agent(
        deps_type=ResearchDeps,
        output_type=ScoutOutput,
        tools=[
            Tool(scout_search, name="web_search"),
            read_page,
            list_links,
            preview_table,
            registry_lookup,
            memory_lookup,
        ],
    )


def vet_specs(
    specs: list[DataSourceSpec], need: DataNeed, deps: ResearchDeps, *, from_memory: bool = False
) -> tuple[list[DataSourceSpec], list[str]]:
    """Keep specs for this need whose sources a tool actually returned (Review Focus 2).

    Memory specs carry their own urls (they were verified before), so only the url's presence
    is required for them.
    """
    kept: list[DataSourceSpec] = []
    rejected: list[str] = []
    for spec in specs:
        if spec.need.strip().lower() not in ("", need.id.lower()):
            rejected.append(f"rejected: spec for other need {spec.need!r}")
            continue
        spec = spec.model_copy(update={"need": need.id})
        if spec.via == "registry":
            entry = deps.catalog.get(spec.registry_key or "")
            if entry is None or not entry.enabled:
                rejected.append(f"rejected: unknown registry key {spec.registry_key!r}")
                continue
        elif not spec.url or not (from_memory or url_allowed(deps, spec.url)):
            shown = deps.ctx.tracer.redact(spec.url or "(none)")
            rejected.append(f"rejected: invented url {shown}")
            continue
        kept.append(spec)
    return kept[:MAX_SPECS], rejected


async def scout(
    need: DataNeed, deps: ResearchDeps, *, hint: str = "", model: Model | None = None
) -> ScoutResult:
    hit = deps.catalog.get(need.series_hint or "")
    if hit is not None and hit.enabled:
        spec = DataSourceSpec(
            need=need.id,
            via="registry",
            registry_key=hit.key,
            publisher=hit.publisher,
            why=f"registry series {hit.key}: {hit.title}",
        )
        return ScoutResult(need_id=need.id, specs=[spec], rejected=[], registry_hit=True)
    remembered = [
        s.model_copy(update={"need": need.id}) for s in deps.memory.lookup(need, today_nairobi())
    ]
    stale: list[str] = []
    if remembered:
        # a remembered spec can have gone stale: its registry key disabled, its url missing
        remembered, stale = vet_specs(remembered, need, deps, from_memory=True)
        for spec in remembered:  # only vetted specs make their urls fetchable
            if spec.url:
                deps.memory_urls.update((spec.url, normalize_url(spec.url)))
        if remembered:
            return ScoutResult(need_id=need.id, specs=remembered, rejected=stale, memory_hit=True)
    agent = build_scout(need)
    agent.instructions(lambda: scout_instructions(today_nairobi()))
    try:
        out = await run_agent(
            agent,
            need_prompt(need, hint),
            deps.ctx,
            stage=STAGE,
            name=f"scout-{need.id}",
            deps=deps,
            usage_limits=LIMITS,
            ledger=deps.ledger,
            group=deps.group,
            model=model,
        )
    except BudgetExceeded:
        raise
    except EngineError as exc:  # usage limit, provider trouble: this need fails soft
        msg = deps.ctx.tracer.redact(exc.message)
        return ScoutResult(
            need_id=need.id, specs=[], rejected=[*stale, f"scout failed: {msg}"], llm_used=True
        )
    specs, rejected = vet_specs(out.specs, need, deps)
    return ScoutResult(need_id=need.id, specs=specs, rejected=stale + rejected, llm_used=True)
