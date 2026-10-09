"""Research orchestrator: plan, scout rounds, figures, claims, verify, challenge, recheck.

Every step writes a checkpoint under `research/` in the run folder; `resume=True` loads a finished
step instead of running it again (once one step runs fresh, every later step does too). Steps fail
soft: a budget or deadline hit ends that step, and the outcome is still built from what exists.
"""

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai.models import Model

from kenya_data_engine.context import RunContext
from kenya_data_engine.data.periods import today_nairobi
from kenya_data_engine.data.registry import load_catalog
from kenya_data_engine.data.stats import FigureBook
from kenya_data_engine.data.store import SeriesStore
from kenya_data_engine.errors import BudgetExceeded
from kenya_data_engine.research.budget import Ledger, Phase
from kenya_data_engine.research.claims import ClaimsOutput, write_claims
from kenya_data_engine.research.evidence import EvidenceBook
from kenya_data_engine.research.figures import FigurePack, figures_for
from kenya_data_engine.research.memory import SourceMemory
from kenya_data_engine.research.models import (
    CandidateClaim,
    Claim,
    Conflict,
    DataNeed,
    NeedStatus,
    ResearchBrief,
    Scorecard,
)
from kenya_data_engine.research.needs import ExecResult, execute, need_status
from kenya_data_engine.research.planner import TopicInput
from kenya_data_engine.research.planner import plan as plan_brief
from kenya_data_engine.research.scout import scout
from kenya_data_engine.research.tiers import tier_for
from kenya_data_engine.research.tools import ResearchDeps
from kenya_data_engine.research.verify import (
    _headline,
    assign_status,
    challenge,
    detect_conflicts,
    entail,
    recheck_verdict,
    verify_static,
)
from kenya_data_engine.runs import RunHandle
from kenya_data_engine.tools.search import build_search

EventFn = Callable[[str, Any], None]

STEPS = ("plan", "rounds", "figures", "claims", "verify", "challenge")
_LATE = (Phase.WRAP_UP, Phase.EXPIRED)
_EXHAUSTED_USD = 0.001  # a scouts group with less than this left cannot pay for another call
_HINT_CHARS = 800


class ResearchOutcome(BaseModel):
    run_id: str
    brief: ResearchBrief
    statuses: list[NeedStatus]
    claims: list[Claim]
    conflicts: list[Conflict]
    verdict: str
    verdict_reasons: list[str]
    scorecard: Scorecard
    gaps: list[str]
    challenge_note: str | None
    figures: FigurePack | None
    stopped_because: str
    # extras the dossier writer needs
    topic: TopicInput | None = None
    log: list[str] = Field(default_factory=list)  # method log, already redacted
    figure_markdown: str = ""


class _Roots(BaseModel):
    """Checkpoint `research/rounds`."""

    statuses: list[NeedStatus]
    results: dict[str, list[ExecResult]]
    notes: dict[str, list[str]] = {}
    gaps: list[str] = []
    rounds: int = 0
    stopped_because: str = ""
    registry_hits: int = 0
    memory_hits: int = 0


class _Verified(BaseModel):
    """Checkpoint `research/verify`."""

    claims: list[Claim]
    conflicts: list[Conflict]


class _Challenge(BaseModel):
    """Checkpoint `research/challenge`."""

    note: str | None = None


class _Plan(BaseModel):
    """Checkpoint `research/plan`."""

    brief: ResearchBrief


def load_topic(run: RunHandle) -> TopicInput | None:
    """The topic a run was started with (for `--resume` without a TOPIC argument)."""
    return run.read("research/topic", TopicInput)


def load_book(ctx: RunContext) -> EvidenceBook:
    """The run's evidence book, reloaded from `research/evidence.json`."""
    book = EvidenceBook(ctx.run.dir, ctx.config.research.tiers, redact=ctx.tracer.redact)
    book.load()
    return book


def _say(on_event: EventFn | None, kind: str, payload: Any) -> None:
    if on_event is None:
        return
    with suppress(Exception):  # a display bug must never stop the research
        on_event(kind, payload)


class _Research:
    def __init__(
        self,
        topic: TopicInput,
        ctx: RunContext,
        *,
        preset: str | None,
        force: bool,
        resume: bool,
        models: dict[str, Model],
        on_event: EventFn | None,
    ) -> None:
        self.topic, self.ctx, self.force, self.resume = topic, ctx, force, resume
        self.models, self.on_event = models, on_event
        self.cfg = ctx.config.research
        self.ledger = Ledger.from_config(self.cfg, preset)
        self.today: date = today_nairobi()
        self.book = EvidenceBook(ctx.run.dir, self.cfg.tiers, redact=ctx.tracer.redact)
        if resume:
            self.book.load()
        self.catalog = load_catalog(ctx.home)
        self.memory = SourceMemory(ctx.home.db_path)
        self.store = SeriesStore(ctx.home.db_path)
        self.log: list[str] = []
        self.memory_urls: set[str] = set()
        self.raw_urls: dict[str, str] = {}
        self.fresh = not resume  # True once a step has run for real
        (ctx.run.dir / "research").mkdir(parents=True, exist_ok=True)
        self._searches: dict[str, Any] = {}

    # --- helpers -------------------------------------------------------------------------

    def deps(self, group: str) -> ResearchDeps:
        if group not in self._searches:
            self._searches[group] = build_search(self.ctx, ledger=self.ledger, group=group)
        return ResearchDeps(
            ctx=self.ctx,
            ledger=self.ledger,
            group=group,
            book=self.book,
            catalog=self.catalog,
            memory=self.memory,
            search=self._searches[group],
            log=self.log,
            memory_urls=self.memory_urls,
            raw_urls=self.raw_urls,
        )

    def step(self, name: str, status: str, detail: str = "") -> None:
        shown = self.ctx.tracer.redact(detail)
        self.log.append(f"{name}: {status}" + (f" — {shown}" if shown else ""))
        _say(self.on_event, "step", {"step": name, "status": status, "detail": shown})
        _say(self.on_event, "ledger", self.ledger.snapshot())

    def load(self, name: str, type_: type[Any]) -> Any:
        """A checkpoint to reuse, or None (resume off, missing, invalid, or an earlier step ran)."""
        if not self.resume or self.fresh:
            return None
        return self.ctx.run.read(f"research/{name}", type_)

    def save(self, name: str, model: BaseModel) -> None:
        self.ctx.run.write(f"research/{name}", model)
        self.book.save()

    def tier_of_series(self, key: str) -> int:
        entry = self.catalog.get(key)
        if entry is not None:
            return int(entry.tier)
        rows = self.store.latest(key)
        return tier_for(rows[0].provenance.url, self.cfg.tiers) if rows else 4

    # --- steps ---------------------------------------------------------------------------

    async def plan(self) -> ResearchBrief:
        saved = self.load("plan", _Plan)
        if saved is not None:
            self.step("plan", "resumed")
            return saved.brief  # type: ignore[no-any-return]
        self.fresh = True
        self.step("plan", "start", self.topic.title)
        started = time.monotonic()
        brief = await plan_brief(
            self.topic, self.deps("planner"), today=self.today, model=self.models.get("planner")
        )
        self.ledger.close("planner")
        self.save("plan", _Plan(brief=brief))
        self.step(
            "plan",
            "done",
            f"verdict {brief.verdict}, {len(brief.data_needs)} needs, "
            f"{time.monotonic() - started:.1f}s",
        )
        return brief

    def _hint(self, need_id: str, state: _Roots) -> str:
        status = next((s for s in state.statuses if s.need_id == need_id), None)
        notes = [*(status.notes if status else []), *state.notes.get(need_id, [])]
        if not notes:
            notes = ["the previous round found no usable source; try other publishers or terms"]
        return "; ".join(dict.fromkeys(notes))[:_HINT_CHARS]

    def _progress(self, state: _Roots) -> int:
        keys = {k for s in state.statuses for k in s.series_keys}
        return sum(s.points for s in state.statuses) + len(self.book.items) + len(keys)

    def _ok(self, res: ExecResult) -> bool:
        spec = res.spec
        if res.error is not None or not (res.series_keys or res.evidence_ids):
            return False
        if res.report is not None and res.report.status != "accepted":
            return False
        if spec.via == "registry":
            entry = self.catalog.get(spec.registry_key or "")
            tier = int(entry.tier) if entry else 4
        else:
            tier = tier_for(spec.url or "", self.cfg.tiers)
        return tier <= 2

    async def _scout_need(
        self,
        need: DataNeed,
        hint: str,
        deps: ResearchDeps,
        sem: asyncio.Semaphore,
        state: _Roots,
        out: list[ExecResult],
    ) -> None:
        """Scout one need; results land in `out` as they come, so a later failure keeps them."""
        async with sem:
            if self.ledger.phase() in _LATE:
                raise BudgetExceeded("research time is nearly used up")
            found = await scout(need, deps, hint=hint, model=self.models.get("scout"))
            state.registry_hits += found.registry_hit
            state.memory_hits += found.memory_hit
            state.notes.setdefault(need.id, []).extend(found.rejected)
            for spec in found.specs:
                res = await execute(spec, need, deps)
                out.append(res)
                try:
                    self.memory.record(need, spec, self._ok(res), self.today)
                except Exception as exc:  # memory is a convenience, never a reason to fail
                    self.log.append(f"memory: could not record: {self.ctx.tracer.redact(str(exc))}")

    async def rounds(self, brief: ResearchBrief) -> _Roots:
        saved: _Roots | None = None
        if self.resume and not self.fresh:
            saved = self.ctx.run.read("research/rounds", _Roots)
        if saved is not None and saved.stopped_because:
            self.step("rounds", "resumed", f"{saved.rounds} rounds, {saved.stopped_because}")
            return saved
        self.fresh = True
        state = saved or _Roots(statuses=[], results={n.id: [] for n in brief.data_needs})
        for n in brief.data_needs:
            state.results.setdefault(n.id, [])
        if saved is not None:  # a crash left a partial run: carry on after the last full round
            self.step("rounds", "resumed", f"continuing after round {state.rounds}")
        if not brief.data_needs:
            state.stopped_because = "no data needs"
            self.ledger.close("scouts")
            self.save("rounds", state)
            return state
        deps = self.deps("scouts")
        sem = asyncio.Semaphore(max(1, self.cfg.scout_concurrency))
        by_need = {n.id: n for n in brief.data_needs}
        out_of_budget = False
        for round_no in range(state.rounds + 1, self.cfg.max_rounds + 1):
            if not state.statuses:
                todo = list(brief.data_needs)
            else:
                open_ids = {s.need_id for s in state.statuses if s.status != "satisfied"}
                todo = [n for n in brief.data_needs if n.id in open_ids]
            if not todo:
                state.stopped_because = "all needs satisfied"
                break
            before = self._progress(state)
            self.step("rounds", "start", f"round {round_no}: {', '.join(n.id for n in todo)}")
            hints = {n.id: self._hint(n.id, state) if round_no > 1 else "" for n in todo}
            partial: dict[str, list[ExecResult]] = {n.id: [] for n in todo}
            outcomes = await asyncio.gather(
                *(self._scout_need(n, hints[n.id], deps, sem, state, partial[n.id]) for n in todo),
                return_exceptions=True,
            )
            state.rounds = round_no
            for need, res in zip(todo, outcomes, strict=True):
                state.results[need.id].extend(partial[need.id])  # keep what landed before a failure
                if isinstance(res, BudgetExceeded):
                    out_of_budget = True
                    msg = f"{need.id}: not scouted, {self.ctx.tracer.redact(res.message)}"
                    state.gaps.append(msg)
                    state.notes.setdefault(need.id, []).append(msg)
                elif isinstance(res, Exception):
                    why = self.ctx.tracer.redact(str(res) or type(res).__name__)
                    msg = f"{need.id}: scout failed: {why}"
                    state.gaps.append(msg)
                    state.notes.setdefault(need.id, []).append(msg)
                elif isinstance(res, BaseException):
                    raise res
                status = need_status(
                    by_need[need.id], state.results[need.id], self.store, self.book, round_no
                )
                state.statuses = [s for s in state.statuses if s.need_id != need.id] + [status]
                _say(self.on_event, "need", status)
            state.statuses.sort(key=lambda s: s.need_id)
            self.save("rounds", state)
            open_n = sum(s.status != "satisfied" for s in state.statuses)
            self.step(
                "rounds",
                "done",
                f"round {round_no}: {len(state.statuses) - open_n} of "
                f"{len(state.statuses)} satisfied",
            )
            if open_n == 0 and len(state.statuses) == len(brief.data_needs):
                state.stopped_because = "all needs satisfied"
            elif out_of_budget:
                state.stopped_because = "budget exhausted during scouting"
            elif round_no >= self.cfg.max_rounds:
                state.stopped_because = f"max rounds ({self.cfg.max_rounds}) reached"
            elif round_no > 1 and self._progress(state) <= before:
                state.stopped_because = "last round added no new points or evidence"
            elif self.ledger.phase() is not Phase.RUNNING:
                state.stopped_because = f"time budget in {self.ledger.phase().value} phase"
            elif self.ledger.remaining_usd("scouts") < _EXHAUSTED_USD:
                state.stopped_because = "scouts budget exhausted"
            if state.stopped_because:
                break
        if not state.stopped_because:
            state.stopped_because = f"max rounds ({self.cfg.max_rounds}) reached"
        for status in state.statuses:
            if status.status != "satisfied":
                need = by_need[status.need_id]
                line = f"{need.id} {status.status}: {need.question}"
                if status.notes:
                    line += f" ({status.notes[0]})"
                state.gaps.append(line)
        self.ledger.close("scouts")
        self.save("rounds", state)
        return state

    def figures(self, brief: ResearchBrief, state: _Roots) -> tuple[FigureBook, FigurePack | None]:
        generic = {
            k for rs in state.results.values() for r in rs if r.generic for k in r.series_keys
        }
        keys = {k for s in state.statuses for k in s.series_keys}
        tiers = {k: self.tier_of_series(k) for k in keys}
        self.step("figures", "start")
        fbook, pack = figures_for(brief.data_needs, state.statuses, self.store, tiers, generic)
        self.ctx.run.write("research/figures", pack)
        self.step("figures", "done", f"{len(fbook.figures)} figures")
        return fbook, (pack if pack.refs else None)

    async def claims_step(
        self, brief: ResearchBrief, pack: FigurePack | None, state: _Roots
    ) -> list[CandidateClaim]:
        saved = self.load("claims", ClaimsOutput)
        if saved is not None:
            self.step("claims", "resumed", f"{len(saved.claims)} candidates")
            return saved.claims  # type: ignore[no-any-return]
        self.fresh = True
        if pack is None and not self.book.items:
            self.step("claims", "skipped", "no figures and no evidence")
            self.save("claims", ClaimsOutput(claims=[]))
            return []
        self.step("claims", "start")
        use = pack or FigurePack(refs=[], markdown="", comparisons_csv="")
        try:
            cands = await write_claims(
                brief,
                self.book,
                use,
                self.deps("claims"),
                statuses=state.statuses,
                model=self.models.get("claims"),
            )
        except BudgetExceeded as exc:
            self.step("claims", "stopped", exc.message)
            state.gaps.append(f"claim writer stopped: {exc.message}")
            return []
        self.save("claims", ClaimsOutput(claims=cands))
        self.step("claims", "done", f"{len(cands)} candidate claims")
        return cands

    async def verify_step(
        self, cands: list[CandidateClaim], pack: FigurePack | None, fbook: FigureBook, state: _Roots
    ) -> _Verified:
        saved = self.load("verify", _Verified)
        if saved is not None:
            self.step("verify", "resumed", f"{len(saved.claims)} claims")
            return saved  # type: ignore[no-any-return]
        self.fresh = True
        self.step("verify", "start", f"{len(cands)} claims")
        use = pack or FigurePack(refs=[], markdown="", comparisons_csv="")
        claims = verify_static(
            cands,
            self.book,
            use,
            fbook,
            tiers=self.cfg.tiers,
            stale_days=self.cfg.stale_days,
            today=self.today,
        )
        try:
            claims = await entail(
                claims, self.book, self.deps("claims"), model=self.models.get("entail")
            )
        except BudgetExceeded as exc:
            self.step("verify", "stopped", f"entailment: {exc.message}")
            state.gaps.append(f"entailment stopped: {exc.message}")
        conflicts = detect_conflicts(claims)
        claims = assign_status(claims, conflicts)
        self.ledger.close("claims")
        result = _Verified(claims=claims, conflicts=conflicts)
        self.save("verify", result)
        counts = {
            s: sum(c.status == s for c in claims)
            for s in ("fact", "inference", "speculation", "refused")
        }
        self.step("verify", "done", ", ".join(f"{n} {s}" for s, n in counts.items()))
        return result

    async def challenge_step(
        self, brief: ResearchBrief, verified: _Verified, state: _Roots
    ) -> str | None:
        saved = self.load("challenge", _Challenge)
        if saved is not None:
            self.step("challenge", "resumed")
            return saved.note  # type: ignore[no-any-return]
        self.fresh = True
        by_need = {s.need_id: s for s in state.statuses}
        head = _headline(brief, verified.claims, by_need)
        if head is None or head.status != "fact":
            self.step("challenge", "skipped", "no fact headline to challenge")
            self.save("challenge", _Challenge())
            return None
        if self.ledger.phase() in _LATE:
            self.step("challenge", "skipped", "time budget nearly used up")
            self.save("challenge", _Challenge())
            return None
        self.step("challenge", "start", head.id)
        try:
            note = await challenge(
                head, brief, self.deps("challenge"), model=self.models.get("challenge")
            )
        except BudgetExceeded as exc:
            self.step("challenge", "stopped", exc.message)
            note = None
        self.save("challenge", _Challenge(note=note))
        self.step("challenge", "done", "contradiction noted" if note else "no contradiction found")
        return note

    # --- outcome -------------------------------------------------------------------------

    def scorecard(
        self, brief: ResearchBrief, state: _Roots, claims: list[Claim], stopped: str
    ) -> Scorecard:
        snap = self.ledger.snapshot()
        satisfied = sum(s.status == "satisfied" for s in state.statuses)
        total = len(brief.data_needs)
        usd = max(snap.usd_spent, self.ctx.tracer.total_cost)
        facts = sum(c.status == "fact" for c in claims)
        return Scorecard(
            preset=snap.preset,
            usd_spent=round(usd, 6),
            usd_cap=snap.usd_total,
            credits_spent=snap.credits_spent,
            credits_cap=snap.credits_total,
            seconds=round(snap.elapsed_s, 2),
            facts=facts,
            inferences=sum(c.status == "inference" for c in claims),
            speculation=sum(c.status == "speculation" for c in claims),
            refused=sum(c.status == "refused" for c in claims),
            facts_per_usd=round(facts / usd, 2) if usd > 0 else None,
            searches_per_satisfied=round(snap.credits_spent / satisfied, 2) if satisfied else None,
            registry_hits=state.registry_hits,
            memory_hits=state.memory_hits,
            cache_hits=0,
            needs_total=total,
            needs_satisfied=satisfied,
            gap_rate=round(1 - satisfied / total, 3) if total and state.statuses else 0.0,
            stopped_because=stopped,
        )

    def outcome(
        self,
        brief: ResearchBrief,
        state: _Roots,
        claims: list[Claim],
        conflicts: list[Conflict],
        note: str | None,
        pack: FigurePack | None,
        fbook: FigureBook | None,
        stopped: str,
        *,
        rechecked: bool = True,
    ) -> ResearchOutcome:
        if rechecked:
            verdict, reasons = recheck_verdict(brief, claims, state.statuses)
        else:  # nothing was researched: the planner's verdict stands
            verdict, reasons = brief.verdict, list(brief.verdict_reasons)
        for k in conflicts:
            if k.resolution == "unresolved":
                state.gaps.append(
                    f"unresolved conflict {k.id}: {k.metric or 'metric'} for {k.entity or 'entity'}"
                )
        return ResearchOutcome(
            run_id=self.ctx.run.run_id,
            brief=brief,
            statuses=state.statuses,
            claims=claims,
            conflicts=conflicts,
            verdict=verdict,
            verdict_reasons=reasons,
            scorecard=self.scorecard(brief, state, claims, stopped),
            gaps=list(dict.fromkeys(state.gaps)),
            challenge_note=note,
            figures=pack,
            stopped_because=stopped,
            topic=self.topic,
            log=list(self.log),
            figure_markdown=fbook.to_markdown() if fbook is not None and fbook.figures else "",
        )


async def research(
    topic: TopicInput,
    ctx: RunContext,
    *,
    preset: str | None = None,
    plan_only: bool = False,
    force: bool = False,
    resume: bool = False,
    models: dict[str, Model] | None = None,
    on_event: EventFn | None = None,
) -> ResearchOutcome:
    run = _Research(
        topic,
        ctx,
        preset=preset,
        force=force,
        resume=resume,
        models=models or {},
        on_event=on_event,
    )
    ctx.run.write("research/topic", topic)
    brief = await run.plan()
    empty = _Roots(statuses=[], results={})
    if brief.verdict == "reject" and not force:
        out = run.outcome(
            brief, empty, [], [], None, None, None, "rejected by the planner", rechecked=False
        )
        ctx.run.write("research/outcome", out)
        run.step("outcome", "done", out.stopped_because)
        return out
    if plan_only:
        out = run.outcome(brief, empty, [], [], None, None, None, "plan only", rechecked=False)
        ctx.run.write("research/outcome", out)
        run.step("outcome", "done", out.stopped_because)
        return out
    state = await run.rounds(brief)
    fbook, pack = run.figures(brief, state)
    cands = await run.claims_step(brief, pack, state)
    verified = await run.verify_step(cands, pack, fbook, state)
    for claim in verified.claims:
        _say(on_event, "claim", claim)
    note = await run.challenge_step(brief, verified, state)
    run.ledger.close("challenge")
    out = run.outcome(
        brief, state, verified.claims, verified.conflicts, note, pack, fbook, state.stopped_because
    )
    ctx.run.write("research/outcome", out)
    run.book.save()
    run.step("outcome", "done", f"verdict {out.verdict}")
    return out
