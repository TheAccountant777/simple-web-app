"""Claim writer: one call, no tools. The model writes templates; code renders every number."""

import re
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from kenya_data_engine.data.periods import today_nairobi
from kenya_data_engine.data.stats import Figure, FigureBook
from kenya_data_engine.llm import load_prompt, run_agent
from kenya_data_engine.research.evidence import EvidenceBook
from kenya_data_engine.research.figures import FigurePack
from kenya_data_engine.research.models import CandidateClaim, DataNeed, NeedStatus, ResearchBrief
from kenya_data_engine.research.tools import ResearchDeps

STAGE = "research_claims"
LIMITS = UsageLimits(request_limit=3)
MAX_CLAIMS = 25
PER_SOURCE_CHARS = 24_000
PLACEHOLDER = re.compile(r"\{\s*F(\d+)\s*\}", re.IGNORECASE)
_STRAY = re.compile(r"\{\s*F\w*\s*\}", re.IGNORECASE)


class ClaimsOutput(BaseModel):
    claims: list[CandidateClaim] = []


def claims_instructions(today: date) -> str:
    return load_prompt("claims").replace("{{today}}", today.isoformat())


_UP = (
    r"rise|rises|rising|rose|risen|climb|climbs|climbed|climbing|grew|grows|growing|"
    r"surge|surges|surged|surging|jump|jumps|jumped|jumping|gain|gains|gained|gaining|"
    r"increase|increases|increased|increasing|higher|soar|soars|soared|soaring|"
    r"spike|spikes|spiked|spiking"
)
_DOWN = (
    r"fall|falls|falling|fell|fallen|decline|declines|declined|declining|"
    r"drop|drops|dropped|dropping|slid|slide|slides|sliding|plunge|plunges|plunged|plunging|"
    r"cut|cuts|cutting|reduce|reduces|reduced|reducing|cheaper|"
    r"decrease|decreases|decreased|decreasing|lower|dip|dips|dipped|dipping|"
    r"slump|slumps|slumped|slumping|ease|eases|eased|easing"
)
UP_WORDS = re.compile(rf"\b(?:{_UP})\b", re.I)
DOWN_WORDS = re.compile(rf"\b(?:{_DOWN})\b", re.I)
CHANGE_KINDS = ("change", "pct_change", "yoy")


def directions(text: str, entities: Iterable[str | None] = ()) -> set[str]:
    """Direction words found in `text` (entity names removed first): a subset of {"up", "down"}."""
    for entity in sorted({e for e in entities if e}, key=len, reverse=True):
        text = re.sub(re.escape(entity), " ", text, flags=re.I)
    found = set()
    if UP_WORDS.search(text):
        found.add("up")
    if DOWN_WORDS.search(text):
        found.add("down")
    return found


def _format(fig: Figure, *, absolute: bool = False) -> str:
    places = 1 if fig.unit in ("pct", "pp") else 2
    value = abs(fig.value) if absolute else fig.value
    q = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if fig.unit == "pct":
        return f"{q}%"
    return f"{q:,} {fig.unit}"


def _signed(fig: Figure) -> str:
    """A change-type figure with its direction from code: "up 3.50 KES", "down 2.10 KES"."""
    if fig.value > 0:
        return f"up {_format(fig, absolute=True)}"
    if fig.value < 0:
        return f"down {_format(fig, absolute=True)}"
    return f"unchanged ({_format(fig)})"


def render(template: str, figures: FigurePack, book: FigureBook) -> str | None:
    """`{F2}` becomes the figure's value and unit. An unknown or unlisted label gives None.

    A change-type figure renders as a signed phrase ("up 3.50 KES", "down 2.10 KES"), so the
    direction always comes from code.
    """
    allowed = {r.label: r for r in figures.refs}
    by_id = {f.id: f for f in book.figures}
    bad = False

    def sub(m: re.Match[str]) -> str:
        nonlocal bad
        label = f"F{int(m[1])}"
        fig = by_id.get(label)
        if label not in allowed or fig is None:
            bad = True
            return ""
        return _signed(fig) if allowed[label].kind in CHANGE_KINDS else _format(fig)

    out = PLACEHOLDER.sub(sub, template)
    if bad or _STRAY.search(out):
        return None
    return out


def _need_block(i: int, need: DataNeed) -> str:
    bits = [f"priority {need.priority}", need.kind, need.question]
    for label, value in (
        ("metric", need.metric),
        ("entities", ", ".join(need.entities)),
        ("unit", need.unit),
        ("period", f"{need.period_start} to {need.period_end}" if need.period_start else None),
    ):
        if value:
            bits.append(f"{label}: {value}")
    return f"N{i}: " + " | ".join(bits)


def claims_prompt(
    brief: ResearchBrief,
    book: EvidenceBook,
    figures: FigurePack,
    statuses: list[NeedStatus] | None = None,
) -> str:
    """Needs, figures, then evidence packets per need (each source once, <= 24k chars)."""
    by_need = {s.need_id: s for s in statuses or []}
    placed: set[str] = set()
    blocks: list[str] = []
    for need in brief.data_needs:
        status = by_need.get(need.id)
        labels = []
        for eid in status.evidence_ids if status else []:
            ev = book.get(eid)
            if ev is not None and ev.label not in placed:
                placed.add(ev.label)
                labels.append(ev.label)
        query = " ".join(filter(None, [need.question, need.metric, *need.entities]))
        if labels:
            blocks.append(book.packet(labels, query, PER_SOURCE_CHARS))
    rest = [ev.label for ev in book.items if ev.label not in placed]
    if rest:
        query = " ".join(filter(None, (f"{n.question} {n.metric or ''}" for n in brief.data_needs)))
        blocks.append(book.packet(rest, query, PER_SOURCE_CHARS))
    needs = "\n".join(_need_block(i, n) for i, n in enumerate(brief.data_needs, start=1))
    return (
        f"Core question: {brief.core_question}\nFraming challenge: {brief.framing_challenge}\n\n"
        f"<needs>\n{needs}\n</needs>\n\n"
        f"<figures>\n{figures.markdown.strip() or '(none)'}\n</figures>\n\n"
        + ("\n\n".join(b for b in blocks if b) or "(no evidence)")
    )


def build_writer() -> Agent[ResearchDeps, ClaimsOutput]:
    return Agent(deps_type=ResearchDeps, output_type=ClaimsOutput)


async def write_claims(
    brief: ResearchBrief,
    book: EvidenceBook,
    figures: FigurePack,
    deps: ResearchDeps,
    *,
    statuses: list[NeedStatus] | None = None,
    model: Model | None = None,
) -> list[CandidateClaim]:
    """One call, no tools, at most 25 claims. `statuses` maps needs to their evidence."""
    agent = build_writer()
    instructions = claims_instructions(today_nairobi())
    agent.instructions(lambda: instructions)
    out = await run_agent(
        agent,
        claims_prompt(brief, book, figures, statuses),
        deps.ctx,
        stage=STAGE,
        name="claims",
        deps=deps,
        usage_limits=LIMITS,
        ledger=deps.ledger,
        group="claims",
        model=model,
    )
    return out.claims[:MAX_CLAIMS]
