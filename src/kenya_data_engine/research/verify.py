"""Verification pipeline: entailment, conflicts, status rules, headline challenge, verdict recheck.

The static checks (steps 2-5) live in `verify_static.py` and are re-exported here.
"""

from dataclasses import replace
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent, Tool
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from kenya_data_engine.data.periods import today_nairobi
from kenya_data_engine.llm import load_prompt, run_agent
from kenya_data_engine.research.evidence import EvidenceBook
from kenya_data_engine.research.models import Claim, Conflict, NeedStatus, ResearchBrief
from kenya_data_engine.research.tiers import tier_for
from kenya_data_engine.research.tools import ResearchDeps, read_page, web_search
from kenya_data_engine.research.verify_static import is_year, verify_static
from kenya_data_engine.tools.grounding import quote_in_text
from kenya_data_engine.tools.numbers import ParsedNumber, find_numbers

__all__ = [
    "assign_status",
    "challenge",
    "detect_conflicts",
    "entail",
    "recheck_verdict",
    "verify_static",
]

ENTAIL_STAGE = "research_entail"
ENTAIL_LIMITS = UsageLimits(request_limit=2)
CHALLENGE_STAGE = "research_challenge"
CHALLENGE_LIMITS = UsageLimits(request_limit=4, tool_calls_limit=3)
BATCH = 5
TOLERANCE = Decimal("0.005")  # values differing by more than 0.5% conflict
_DEAD = ("refused", "speculation")


# --- entailment -------------------------------------------------------------------------------


class Verdict(BaseModel):
    claim: str
    verdict: Literal["yes", "partial", "no"]


class EntailOutput(BaseModel):
    verdicts: list[Verdict] = []


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;")


def entail_prompt(batch: list[Claim]) -> str:
    """Only claim text and quote: no URL, tier, publisher or page (Pitfall 8)."""
    return "\n".join(
        f'<claim id="{c.id}">\n<text>{_esc(c.text)}</text>\n'
        f'<quote untrusted="true">{_esc(c.quote or "")}</quote>\n</claim>'
        for c in batch
    )


def _quote_based(c: Claim) -> bool:
    return c.status not in _DEAD and c.quote is not None and c.evidence_id is not None


async def entail(
    claims: list[Claim],
    book: EvidenceBook,
    deps: ResearchDeps,
    *,
    model: Model | None = None,
) -> list[Claim]:
    """Judge quote-based claims that passed the static checks, five per call."""
    out = [c.model_copy(deep=True) for c in claims]
    todo: list[Claim] = []
    for c in out:
        if c.status in _DEAD:
            continue
        if c.quote is None and c.figure_ids:
            c.entailment = "yes"  # built by code from computed figures
        elif _quote_based(c) and c.quote_grounded and c.numbers_ok and c.entity_period_ok:
            todo.append(c)
    agent: Agent[ResearchDeps, EntailOutput] = Agent(
        deps_type=ResearchDeps, output_type=EntailOutput
    )
    instructions = load_prompt("entail").replace("{{today}}", today_nairobi().isoformat())
    agent.instructions(lambda: instructions)
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        try:
            res = await run_agent(
                agent,
                entail_prompt(batch),
                deps.ctx,
                stage=ENTAIL_STAGE,
                name="entail",
                deps=deps,
                usage_limits=ENTAIL_LIMITS,
                ledger=deps.ledger,
                group="claims",
                model=model,
            )
        except Exception as exc:  # fail soft: the claims stay unjudged and cannot become facts
            msg = deps.ctx.tracer.redact(str(exc) or type(exc).__name__)
            for c in batch:
                c.reasons.append(f"entailment not run: {msg}")
            continue
        verdicts = {v.claim.strip().upper(): v.verdict for v in res.verdicts}
        for c in batch:
            v = verdicts.get(c.id)
            if v is None:
                c.reasons.append("entailment: judge gave no verdict")
                continue
            c.entailment = v
            if v == "no":
                c.status = "refused"
                c.reasons.append("entailment: the quote does not support the claim")
            elif v == "partial":
                c.reasons.append("entailment: the quote supports the claim only in part")
    return out


# --- conflicts --------------------------------------------------------------------------------


def _primary(c: Claim) -> ParsedNumber | None:
    """The claim's headline number: the first one in its text that is not a bare year."""
    return next((n for n in find_numbers(c.text) if not is_year(n)), None)


def _close(a: Decimal, b: Decimal) -> bool:
    top = max(abs(a), abs(b))
    return top == 0 or abs(a - b) / top <= TOLERANCE


def _key(c: Claim) -> tuple[str, str, str, str] | None:
    n = _primary(c)
    if c.metric is None or c.entity is None or n is None:
        return None
    return (
        c.metric.strip().lower(),
        c.entity.strip().lower(),
        (c.period or "").strip().lower(),
        n.unit,
    )


def detect_conflicts(claims: list[Claim]) -> list[Conflict]:
    """Group by (metric, entity, period); values differing by over 0.5% conflict."""
    groups: dict[tuple[str, str, str, str], list[Claim]] = {}
    for c in claims:
        k = _key(c)
        if c.status not in _DEAD and k is not None:
            groups.setdefault(k, []).append(c)
    out: list[Conflict] = []
    for (metric, entity, period, _unit), members in groups.items():
        clusters: list[list[Claim]] = []
        for c in members:
            v = _value(c)
            for cl in clusters:
                if _close(_value(cl[0]), v):
                    cl.append(c)
                    break
            else:
                clusters.append([c])
        if len(clusters) < 2:
            continue
        conflict = _resolve(f"K{len(out) + 1}", members, clusters)
        conflict.metric, conflict.entity, conflict.period = metric, entity, period or None
        out.append(conflict)
    return out


def _value(c: Claim) -> Decimal:
    n = _primary(c)
    assert n is not None  # only claims with a number are grouped
    return n.value


def _resolve(cid: str, members: list[Claim], clusters: list[list[Claim]]) -> Conflict:
    values = [str(_value(cl[0]).normalize()) for cl in clusters]
    ids = [c.id for c in members]
    best = min(c.tier for c in members)
    top = [c for c in members if c.tier == best]
    top_clusters = [cl for cl in clusters if any(c.tier == best for c in cl)]
    if len(top_clusters) == 1:
        return Conflict(
            id=cid, claim_ids=ids, values=values, resolution="prefer_higher_tier", kept=top[0].id
        )
    if all(c.published for c in top):
        newest = max(c.published for c in top if c.published)
        winners = [c for c in top if c.published == newest]
        if len({id(_cluster_of(clusters, c)) for c in winners}) == 1:
            return Conflict(
                id=cid, claim_ids=ids, values=values, resolution="prefer_newer", kept=winners[0].id
            )
    return Conflict(id=cid, claim_ids=ids, values=values, resolution="unresolved")


def _cluster_of(clusters: list[list[Claim]], c: Claim) -> list[Claim]:
    return next(cl for cl in clusters if any(m.id == c.id for m in cl))


# --- status -----------------------------------------------------------------------------------


def _agree(a: Claim, b: Claim) -> bool:
    na, nb = _primary(a), _primary(b)
    if na is not None and nb is not None:
        return na.unit == nb.unit and _close(na.value, nb.value)
    return a.legal_stage is not None and a.legal_stage == b.legal_stage


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _same_subject(a: Claim, b: Claim) -> bool:
    if a.metric is None or a.entity is None or b.metric is None or b.entity is None:
        return False
    return (_norm(a.metric), _norm(a.entity), _norm(a.period)) == (
        _norm(b.metric),
        _norm(b.entity),
        _norm(b.period),
    )


def _sound(c: Claim) -> bool:
    """Passes every fact condition except tier and conflicts."""
    figure_only = c.quote is None and bool(c.figure_ids)
    return (
        c.status not in _DEAD
        and (c.quote_grounded or figure_only)
        and c.numbers_ok
        and c.entity_period_ok
        and c.entailment == "yes"
        and not c.stale
        and not c.generic
    )


def assign_status(claims: list[Claim], conflicts: list[Conflict]) -> list[Claim]:
    """The fact rule (spec 6.2 step 8); idempotent."""
    by_id = {c.id: c for c in claims}
    blocked: dict[str, str] = {}  # claim id -> reason it cannot be a fact
    attached: dict[str, list[str]] = {}
    for k in conflicts:
        kept = by_id.get(k.kept) if k.kept else None
        for cid in k.claim_ids:
            attached.setdefault(cid, []).append(k.id)
            c = by_id.get(cid)
            if c is None:
                continue
            if kept is None:
                blocked[cid] = f"unresolved conflict {k.id}"
            elif not _agree(c, kept):
                blocked[cid] = f"conflict {k.id}: contradicted by {k.kept} ({k.resolution})"
    tier1 = [c for c in claims if c.tier == 1 and _sound(c) and c.id not in blocked]
    out: list[Claim] = []
    for src in claims:
        c = src.model_copy(deep=True)
        c.conflicts = attached.get(c.id, c.conflicts)
        out.append(c)
        if c.status in _DEAD:
            continue
        if c.entailment == "no":
            c.status = "refused"
            c.reasons.append("entailment: the quote does not support the claim")
            continue
        if c.sensitive and (c.tier != 1 or c.entailment != "yes"):
            c.status = "refused"
            c.reasons.append("sensitive claim needs tier 1 and entailment yes")
            continue
        missing: list[str] = []
        figure_only = c.quote is None and bool(c.figure_ids)
        if not (c.quote_grounded or figure_only):
            missing.append("quote not grounded")
        if not c.numbers_ok:
            missing.append("numbers not checked")
        if not c.entity_period_ok:
            missing.append("entity or period not checked")
        if c.entailment != "yes":
            missing.append("entailment is not yes")
        if c.tier != 1:
            corroborated = (
                c.tier in (2, 3)
                and c.claim_type != "legal_status"
                and any(_same_subject(c, d) and _agree(c, d) for d in tier1 if d.id != c.id)
            )
            if not corroborated:
                missing.append(
                    "a legal status needs a tier 1 source"
                    if c.claim_type == "legal_status"
                    else f"tier {c.tier} source without tier 1 corroboration"
                )
        if c.stale:
            missing.append("stale")
        if c.generic:
            missing.append("generic extraction")
        if c.id in blocked:
            missing.append(blocked[c.id])
        if missing:
            c.status = "inference"
            c.reasons.extend(m for m in missing if m not in c.reasons)
        else:
            c.status = "fact"
    return out


# --- headline challenge -----------------------------------------------------------------------


class ChallengeOutput(BaseModel):
    contradiction: bool = False
    url: str | None = None
    quote: str | None = None


async def challenge(
    headline: Claim,
    brief: ResearchBrief,
    deps: ResearchDeps,
    *,
    model: Model | None = None,
) -> str | None:
    """One targeted search for a Tier 1 contradiction. Returns a note built by code from a
    grounded quote, or None. It never changes a claim."""
    agent: Agent[ResearchDeps, ChallengeOutput] = Agent(
        deps_type=ResearchDeps,
        output_type=ChallengeOutput,
        tools=[Tool(web_search, name="web_search"), Tool(read_page, name="read_page")],
    )
    instructions = load_prompt("challenge").replace("{{today}}", today_nairobi().isoformat())
    agent.instructions(lambda: instructions)
    local = replace(deps, group="challenge")
    prompt = (
        f"Headline claim: {headline.text}\n"
        f"Entity: {headline.entity}; metric: {headline.metric}; period: {headline.period}\n"
        f"Framing challenge: {brief.framing_challenge}"
    )
    try:
        res = await run_agent(
            agent,
            prompt,
            deps.ctx,
            stage=CHALLENGE_STAGE,
            name="challenge",
            deps=local,
            usage_limits=CHALLENGE_LIMITS,
            ledger=deps.ledger,
            group="challenge",
            model=model,
        )
    except Exception as exc:  # fail soft: no note
        deps.log.append(deps.ctx.tracer.redact(f"challenge: {exc}"))
        return None
    if not res.contradiction or not res.url or not res.quote:
        return None
    ev = next((e for e in deps.book.items if res.url in (e.url, e.final_url)), None)
    if ev is None or tier_for(ev.final_url, deps.ctx.config.research.tiers) != 1:
        return None
    if not quote_in_text(res.quote, deps.book.text(ev)):
        return None
    return (
        f"Tier 1 source {ev.publisher} ({ev.final_url}) may contradict the headline: "
        f"“{res.quote.strip()}”"
    )


# --- verdict ----------------------------------------------------------------------------------


def recheck_verdict(
    brief: ResearchBrief, claims: list[Claim], statuses: list[NeedStatus]
) -> tuple[str, list[str]]:
    """Downgrade in code: a verdict is only as good as its priority-1 needs and headline."""
    verdict, reasons = brief.verdict, list(brief.verdict_reasons)
    if verdict == "reject":
        return verdict, reasons
    by_need = {s.need_id: s for s in statuses}
    p1 = [n for n in brief.data_needs if n.priority == 1]
    missing = [n.id for n in p1 if (s := by_need.get(n.id)) is None or s.status == "not_found"]
    if p1 and len(missing) == len(p1):
        reasons.append("every priority-1 need came back not_found")
        return "reject", reasons
    if verdict != "supported":
        return verdict, reasons
    if missing:
        reasons.append(f"priority-1 need not found: {', '.join(missing)}")
        verdict = "reframed"
    headline = _headline(brief, claims, by_need)
    if headline is not None and headline.status != "fact":
        reasons.append(f"headline claim {headline.id} is {headline.status}, not a fact")
        verdict = "reframed"
    elif headline is None and p1:
        reasons.append("no claim on a priority-1 need could be a fact")
        verdict = "reframed"
    return verdict, reasons


def _headline(
    brief: ResearchBrief, claims: list[Claim], by_need: dict[str, NeedStatus]
) -> Claim | None:
    """The first claim (writer order) on a priority-1 need that passed grounding."""
    evidence: set[str] = set()
    figures: set[str] = set()
    for n in brief.data_needs:
        s = by_need.get(n.id)
        if n.priority == 1 and s is not None:
            evidence.update(s.evidence_ids)
            figures.update(s.figure_ids)
    for c in claims:
        if c.status in _DEAD:
            continue
        if (c.evidence_id and c.evidence_id in evidence) or figures.intersection(c.figure_ids):
            return c
    return None
