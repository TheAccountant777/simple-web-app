"""Dossier writer: a finished research outcome becomes a folder a journalist can check.

`briefs/<date>/NN-<slug>/` holds the README (verdict, top facts, chart readiness, gaps,
scorecard, a 15-minute check), the data CSVs, the sources with tiers and hashes, and
`dossier.json` for machines. A rejected dossier is short: README, brief and dossier.json.
Every text written passes through `Tracer.redact` first.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import date, datetime
from importlib import metadata, resources
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from kenya_data_engine import __version__
from kenya_data_engine.context import RunContext
from kenya_data_engine.data.csvsafe import write_csv
from kenya_data_engine.data.models import StoredObservation
from kenya_data_engine.data.registry import CatalogEntry, load_catalog
from kenya_data_engine.data.store import BlobStore, SeriesStore
from kenya_data_engine.research.evidence import EvidenceBook
from kenya_data_engine.research.memory import TopicMemory
from kenya_data_engine.research.models import (
    Claim,
    DataNeed,
    DossierMeta,
    NeedStatus,
    ResearchBrief,
    TextEvidence,
)
from kenya_data_engine.research.orchestrator import ResearchOutcome
from kenya_data_engine.research.tiers import host_of, tier_for
from kenya_data_engine.tools.grounding import quote_in_text

SLUG_MAX = 50
TOP_FACTS = 3
CSV_COLUMNS = ["period", "entity", "metric", "value", "unit", "source_url"]
_PROMPTS = ("planner", "scout", "claims", "entail", "challenge", "_primer")
_PAGE = re.compile(r"\[page (\d+)\]")
_DATE_DIR = re.compile(r"^(\d{2})-")


class SourceEntry(BaseModel):
    kind: Literal["evidence", "series"]
    id: str  # evidence id, or the series key
    url: str
    tier: int
    publisher: str
    sha256: str
    vintage: int | None = None
    extractor: str | None = None
    published: date | None = None
    retrieved_at: datetime | None = None
    attribution: str


class DossierDoc(DossierMeta):
    """dossier.json: the reproducibility meta plus everything the folder says."""

    verdict: str
    verdict_reasons: list[str]
    brief: ResearchBrief
    statuses: list[NeedStatus]
    claims: list[Claim]
    conflicts: list[Any] = Field(default_factory=list)
    scorecard: dict[str, Any]
    gaps: list[str]
    challenge_note: str | None = None
    stopped_because: str = ""
    sources: list[SourceEntry] = Field(default_factory=list)
    charts: list[dict[str, Any]] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)


# --- paths ------------------------------------------------------------------------------------


def slugify(text: str) -> str:
    """ASCII-fold, lowercase, `[a-z0-9]+` joined by "-", at most 50 characters."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = "-".join(re.findall(r"[a-z0-9]+", folded.lower()))[:SLUG_MAX].strip("-")
    return slug or "topic"


def dossier_dir(briefs_dir: Path, today: date, slug: str) -> Path:
    """`<briefs>/<date>/NN-<slug>` with the next free NN for that date."""
    day = briefs_dir / today.isoformat()
    taken = [int(m[1]) for p in day.glob("*") if (m := _DATE_DIR.match(p.name)) and p.is_dir()]
    return day / f"{max(taken, default=0) + 1:02d}-{slug}"


# --- reproducibility --------------------------------------------------------------------------


def _git_sha() -> str | None:
    """The commit the package was installed from (PEP 610 direct_url.json), or None."""
    try:
        raw = metadata.distribution("kenya-data-engine").read_text("direct_url.json")
        sha = json.loads(raw or "{}").get("vcs_info", {}).get("commit_id")
        return str(sha) if sha else None
    except (metadata.PackageNotFoundError, ValueError, OSError):
        return None


def _prompt_hashes() -> dict[str, str]:
    base = resources.files("kenya_data_engine.prompts")
    out: dict[str, str] = {}
    for name in _PROMPTS:
        try:
            out[name] = hashlib.sha256(base.joinpath(f"{name}.md").read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def _config_hash(ctx: RunContext) -> str:
    text = ctx.config.model_dump_json()
    return hashlib.sha256(text.encode()).hexdigest()


# --- small helpers ----------------------------------------------------------------------------


def _one_line(text: str, limit: int | None = None) -> str:
    text = " ".join(text.split())
    return text if limit is None or len(text) <= limit else text[: limit - 1] + "…"


def _cell(text: str) -> str:
    return _one_line(text).replace("|", "\\|")


def _publisher(url: str) -> str:
    return host_of(url).removeprefix("www.") or "unknown"


def _in_range(o: StoredObservation, need: DataNeed) -> bool:
    if need.period_start is not None and o.period.end < need.period_start:
        return False
    return need.period_end is None or o.period.start <= need.period_end


# --- data and sources -------------------------------------------------------------------------


def _need_rows(need: DataNeed, status: NeedStatus, store: SeriesStore) -> list[StoredObservation]:
    wanted = {e.strip().lower() for e in need.entities}
    rows: list[StoredObservation] = []
    for key in dict.fromkeys(status.series_keys):
        rows.extend(
            o
            for o in store.latest(key)
            if (not wanted or o.entity.strip().lower() in wanted) and _in_range(o, need)
        )
    return sorted(rows, key=lambda o: (o.entity, o.metric, o.period.start))


def _csv_for(rows: Iterable[StoredObservation]) -> str:
    body = [(o.period.label, o.entity, o.metric, o.value, o.unit, o.provenance.url) for o in rows]
    return write_csv(body, CSV_COLUMNS)


def _series_tier(
    key: str, url: str, catalog: dict[str, CatalogEntry], tiers: dict[str, int]
) -> int:
    entry = catalog.get(key)
    return int(entry.tier) if entry is not None else tier_for(url, tiers)


def _sources(
    outcome: ResearchOutcome,
    book: EvidenceBook,
    store: SeriesStore,
    catalog: dict[str, CatalogEntry],
    tiers: dict[str, int],
) -> list[SourceEntry]:
    out: list[SourceEntry] = []
    for ev in book.items:
        out.append(
            SourceEntry(
                kind="evidence",
                id=ev.id,
                url=ev.final_url,
                tier=ev.tier,
                publisher=ev.publisher,
                sha256=ev.blob_sha256,
                published=ev.published,
                retrieved_at=ev.retrieved_at,
                extractor="text",
                attribution=f"Source: {ev.publisher}",
            )
        )
    seen: set[tuple[str, str, str]] = set()
    for status in outcome.statuses:
        for key in dict.fromkeys(status.series_keys):
            entry = catalog.get(key)
            latest: dict[tuple[str, str], StoredObservation] = {}
            for o in store.latest(key):
                prev = latest.get((o.provenance.url, o.provenance.blob_sha256))
                if prev is None or o.vintage > prev.vintage:
                    latest[(o.provenance.url, o.provenance.blob_sha256)] = o
            for (url, sha), o in latest.items():
                if (key, url, sha) in seen:
                    continue
                seen.add((key, url, sha))
                vintage = max(
                    x.vintage
                    for x in store.latest(key)
                    if (x.provenance.url, x.provenance.blob_sha256) == (url, sha)
                )
                publisher = entry.publisher if entry is not None else _publisher(url)
                out.append(
                    SourceEntry(
                        kind="series",
                        id=key,
                        url=url,
                        tier=_series_tier(key, url, catalog, tiers),
                        publisher=publisher,
                        sha256=sha,
                        vintage=vintage,
                        extractor=o.provenance.extractor,
                        published=o.provenance.published,
                        retrieved_at=o.provenance.retrieved_at,
                        attribution=f"Source: {publisher}",
                    )
                )
    return out


# --- locating a fact's source -------------------------------------------------------------------


def _quote_page(text: str, quote: str) -> str | None:
    """The `[page N]` marker before the quote in PDF evidence text, if it can be found."""
    if not quote_in_text(quote, text):
        return None
    probe = " ".join(quote.split())
    idx = text.find(probe)
    if idx < 0:
        words = probe.split()
        for n in range(len(words), 2, -1):
            idx = text.find(" ".join(words[:n]))
            if idx >= 0:
                break
    if idx < 0:
        return None
    pages = _PAGE.findall(text[:idx])
    return f"page {pages[-1]}" if pages else None


def _fact_source(
    claim: Claim, outcome: ResearchOutcome, book: EvidenceBook, store: SeriesStore
) -> tuple[str, str]:
    """(url, locator) the reader should open to check a claim."""
    ev: TextEvidence | None = book.get(claim.evidence_id) if claim.evidence_id else None
    if ev is not None and claim.quote:
        loc = _quote_page(book.text(ev), claim.quote) or "in the page text"
        return ev.final_url, loc
    refs = {r.figure_id: r for r in (outcome.figures.refs if outcome.figures else [])}
    for fid in claim.figure_ids:
        ref = refs.get(fid)
        if ref is None:
            continue
        for o in store.latest(ref.series):
            if o.entity == ref.entity and o.period.label == ref.period_label:
                return o.provenance.url, o.provenance.locator
    return "", ""


# --- text files ---------------------------------------------------------------------------------


def _brief_md(brief: ResearchBrief) -> str:
    lines = [
        f"# Research brief: {brief.topic}",
        "",
        f"**Verdict:** {brief.verdict}",
        "",
        f"**Core question:** {brief.core_question}",
        "",
        f"**Framing challenge:** {brief.framing_challenge}",
    ]
    if brief.reframe:
        lines += ["", f"**Suggested reframe:** {brief.reframe}"]
    if brief.verdict_reasons:
        lines += ["", "## Verdict reasons", *(f"- {r}" for r in brief.verdict_reasons)]
    lines += ["", "## Angles"]
    lines += [
        f"- **{a.label}**{' (contrarian)' if a.contrarian else ''}: {a.thesis}"
        for a in brief.angles
    ]
    lines += ["", "## Chart concepts"]
    lines += [
        f"- **{c.id}** ({c.relationship}): {c.idea}"
        + (f" [needs {', '.join(c.needs)}]" if c.needs else "")
        for c in brief.chart_concepts
    ]
    lines += ["", "## Data needs"]
    for n in brief.data_needs:
        bits = [n.kind, f"priority {n.priority}"]
        if n.metric:
            bits.append(f"metric: {n.metric}")
        if n.entities:
            bits.append("entities: " + ", ".join(n.entities))
        if n.series_hint:
            bits.append(f"registry hint: {n.series_hint}")
        lines.append(f"- **{n.id}** {n.question} ({'; '.join(bits)})")
    return "\n".join(lines) + "\n"


def _gaps_md(outcome: ResearchOutcome) -> str:
    lines = ["# Gaps", ""]
    open_needs = [s for s in outcome.statuses if s.status != "satisfied"]
    by_id = {n.id: n for n in outcome.brief.data_needs}
    if open_needs:
        lines.append("## Needs not satisfied")
        for s in open_needs:
            need = by_id.get(s.need_id)
            lines.append(f"- **{s.need_id}** {s.status}: {need.question if need else ''}")
            lines += [f"  - {_one_line(n)}" for n in s.notes]
        lines.append("")
    quarantined = [(s.need_id, n) for s in outcome.statuses for n in s.notes if "quarantined" in n]
    if quarantined:
        lines.append("## Quarantined tables")
        lines += [f"- {nid}: {_one_line(n)}" for nid, n in quarantined]
        lines.append("")
    if outcome.gaps:
        lines.append("## Everything recorded")
        lines += [f"- {_one_line(g)}" for g in outcome.gaps]
        lines.append("")
    if len(lines) == 2:
        lines.append("No gaps were recorded.")
    return "\n".join(lines).rstrip() + "\n"


def _verification_md(outcome: ResearchOutcome) -> str:
    lines = [
        "# Verification log",
        "",
        f"Run {outcome.run_id}. Preset {outcome.scorecard.preset}. "
        f"Stopped because: {outcome.stopped_because}.",
        "",
        "## Steps",
        *(f"- {_one_line(x)}" for x in outcome.log),
        "",
        "## Claims",
    ]
    for c in outcome.claims:
        lines.append(f"### {c.id} {c.status}: {_one_line(c.text)}")
        lines.append(f"- tier {c.tier}, entailment {c.entailment}, type {c.claim_type}")
        if c.quote:
            lines.append(f"- quote: “{_one_line(c.quote, 400)}”")
        lines += [f"- {_one_line(r)}" for r in c.reasons]
    if not outcome.claims:
        lines.append("No claims were written.")
    lines += ["", "## Conflicts"]
    for k in outcome.conflicts:
        lines.append(
            f"- {k.id}: {', '.join(k.claim_ids)} on {k.metric or '?'} for {k.entity or '?'} "
            f"({k.resolution}, kept {k.kept or 'none'})"
        )
    if not outcome.conflicts:
        lines.append("None.")
    lines += ["", "## Headline challenge", outcome.challenge_note or "No contradiction found."]
    return "\n".join(lines) + "\n"


def _claims_json(outcome: ResearchOutcome, sources: list[SourceEntry]) -> dict[str, Any]:
    def group(status: str) -> list[Any]:
        return [c.model_dump(mode="json") for c in outcome.claims if c.status == status]

    return {
        "facts": group("fact"),
        "inferences": group("inference"),
        "speculation": group("speculation"),
        "refused": group("refused"),
        "conflicts": [k.model_dump(mode="json") for k in outcome.conflicts],
        "evidence": [s.model_dump(mode="json") for s in sources if s.kind == "evidence"],
    }


# --- README -----------------------------------------------------------------------------------


def _chart_rows(
    brief: ResearchBrief, outcome: ResearchOutcome, csv_names: dict[str, str]
) -> list[dict[str, Any]]:
    by_status = {s.need_id: s.status for s in outcome.statuses}
    rows: list[dict[str, Any]] = []
    for concept in brief.chart_concepts:
        need_ids = list(
            dict.fromkeys(
                [*concept.needs]
                + [n.id for n in brief.data_needs if concept.id in n.chart_concepts]
            )
        )
        states = [by_status.get(n, "not_found") for n in need_ids]
        if states and all(s == "satisfied" for s in states):
            state = "ready"
        elif any(s != "not_found" for s in states):
            state = "partial"
        else:
            state = "not possible"
        files = [csv_names[n] for n in need_ids if n in csv_names]
        rows.append(
            {
                "id": concept.id,
                "idea": concept.idea,
                "relationship": concept.relationship,
                "state": state,
                "csv": files,
            }
        )
    return rows


def _readme(
    outcome: ResearchOutcome,
    facts: list[tuple[Claim, str, str]],
    charts: list[dict[str, Any]],
    n_refused: int,
    short: bool,
) -> str:
    brief, sc = outcome.brief, outcome.scorecard
    lines = [f"# {brief.topic}", "", f"**Verdict: {outcome.verdict}**", ""]
    lines += [f"- {_one_line(r)}" for r in outcome.verdict_reasons]
    if brief.reframe:
        lines += ["", f"Suggested reframe: {_one_line(brief.reframe)}"]
    lines += ["", f"**Core question:** {brief.core_question}", ""]
    if short:
        lines += [
            "This topic was not researched further, so there is no data folder.",
            "",
            f"Stopped because: {outcome.stopped_because}.",
            "",
            f"Cost: ${sc.usd_spent:.4f} of ${sc.usd_cap:.2f}, {sc.credits_spent} search credits.",
        ]
        return "\n".join(lines) + "\n"
    lines += ["## Top facts", ""]
    if facts:
        for i, (c, url, loc) in enumerate(facts, start=1):
            link = f"[{_publisher(url)}]({url})" if url else "(no link)"
            lines.append(f"{i}. **{_one_line(c.text)}**")
            lines.append(f"   - Source: {link}, Tier {c.tier}" + (f", {loc}" if loc else ""))
            if c.quote:
                lines.append(f"   - Quote: “{_one_line(c.quote, 300)}”")
    else:
        lines.append("No claim passed every check, so there are no facts to lead with.")
    lines += [
        "",
        "## Chart concepts",
        "",
        "| Concept | Chart | Status | CSV |",
        "| --- | --- | --- | --- |",
    ]
    for ch in charts:
        csv = ", ".join(f"`{n}`" for n in ch["csv"]) or "-"
        lines.append(f"| {ch['id']} | {_cell(ch['idea'])} | {ch['state']} | {csv} |")
    lines += ["", "## Gaps and conflicts", ""]
    items = [_one_line(g) for g in outcome.gaps] + [
        f"Conflict {k.id}: {k.metric or 'metric'} for {k.entity or 'entity'} ({k.resolution})"
        for k in outcome.conflicts
    ]
    lines += [f"- {x}" for x in items] or ["- None recorded."]
    if outcome.challenge_note:
        lines += ["", f"Headline challenge: {_one_line(outcome.challenge_note)}"]
    lines += [
        "",
        "## Scorecard",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Facts / inferences / speculation / refused | {sc.facts} / {sc.inferences} / "
        f"{sc.speculation} / {sc.refused} |",
        f"| Needs satisfied | {sc.needs_satisfied} of {sc.needs_total} "
        f"(gap rate {sc.gap_rate:.0%}) |",
        f"| Cost | ${sc.usd_spent:.4f} of ${sc.usd_cap:.2f} |",
        f"| Search credits | {sc.credits_spent} of {sc.credits_cap} |",
        f"| Time | {sc.seconds:.0f}s |",
        f"| Registry / memory hits | {sc.registry_hits} / {sc.memory_hits} |",
        "| Facts per dollar | "
        + ("n/a" if sc.facts_per_usd is None else f"{sc.facts_per_usd:.1f}")
        + " |",
        f"| Stopped because | {_cell(sc.stopped_because)} |",
        "",
        "## 15-minute check",
        "",
        "Before you rely on this dossier:",
        "",
    ]
    lines.append(
        "1. Open each top-fact link above and confirm the quote and the number on the page."
    )
    for c, url, loc in facts:
        if url:
            lines.append(f"   - {c.id}: {url}" + (f" ({loc})" if loc else ""))
    lines += [
        "2. Check each chart CSV in `data/` against its source: open the URL in `source_url` "
        "and compare a few rows.",
        f"3. Read the refusals in `research/verification.md` ({n_refused} claims were refused) "
        "and make sure nothing you wanted was dropped for a good reason.",
        "4. Read `gaps.md` and decide whether a missing piece changes your story.",
    ]
    return "\n".join(lines) + "\n"


# --- the writer -------------------------------------------------------------------------------


def _put(root: Path, rel: str, text: str, ctx: RunContext, files: list[str]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ctx.tracer.redact(text), encoding="utf-8")
    files.append(rel)


def write_dossier(
    outcome: ResearchOutcome, ctx: RunContext, book: EvidenceBook, *, today: date
) -> Path:
    """Write the dossier folder and record the topic in topic memory. Returns the folder."""
    title = outcome.topic.title if outcome.topic else outcome.brief.topic
    root = dossier_dir(ctx.home.briefs_dir, today, slugify(title))
    root.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    store = SeriesStore(ctx.home.db_path)
    catalog = load_catalog(ctx.home)
    short = not outcome.statuses and not outcome.claims  # nothing was researched
    tiers = ctx.config.research.tiers

    sources: list[SourceEntry] = []
    charts: list[dict[str, Any]] = []
    facts: list[tuple[Claim, str, str]] = []
    if not short:
        sources = _sources(outcome, book, store, catalog, tiers)
        csv_names: dict[str, str] = {}
        by_status = {s.need_id: s for s in outcome.statuses}
        for need in outcome.brief.data_needs:
            status = by_status.get(need.id)
            if need.kind != "series" or status is None or status.status == "not_found":
                continue
            rows = _need_rows(need, status, store)
            if not rows:
                continue
            name = f"{need.id}-{slugify(need.metric or need.question)}.csv"
            csv_names[need.id] = name
            _put(root, f"data/{name}", _csv_for(rows), ctx, files)
        charts = _chart_rows(outcome.brief, outcome, csv_names)
        for c in [c for c in outcome.claims if c.status == "fact"][:TOP_FACTS]:
            url, loc = _fact_source(c, outcome, book, store)
            facts.append((c, url, loc))
        figs = outcome.figures
        _put(
            root,
            "stats.md",
            "# Figures\n\n"
            + (
                outcome.figure_markdown
                or (figs.markdown if figs else "")
                or "No figures were computed.\n"
            ),
            ctx,
            files,
        )
        _put(
            root,
            "research/comparisons.csv",
            figs.comparisons_csv if figs else write_csv([], ["series"]),
            ctx,
            files,
        )
        _put(root, "gaps.md", _gaps_md(outcome), ctx, files)
        _put(
            root,
            "research/claims.json",
            json.dumps(_claims_json(outcome, sources), indent=2, ensure_ascii=False),
            ctx,
            files,
        )
        _put(root, "research/verification.md", _verification_md(outcome), ctx, files)
        _put(
            root,
            "sources.json",
            json.dumps([s.model_dump(mode="json") for s in sources], indent=2),
            ctx,
            files,
        )
    _put(root, "brief.md", _brief_md(outcome.brief), ctx, files)
    n_refused = sum(c.status == "refused" for c in outcome.claims)
    _put(root, "README.md", _readme(outcome, facts, charts, n_refused, short), ctx, files)

    stage = ctx.config.llm.stages.get("research_claims")
    doc = DossierDoc(
        run_id=outcome.run_id,
        topic=title,
        created_at=datetime.now().astimezone(),
        engine_version=__version__,
        git_sha=_git_sha(),
        config_hash=_config_hash(ctx),
        prompt_hashes=_prompt_hashes(),
        model=stage.model if stage else "unknown",
        path=str(root),
        verdict=outcome.verdict,
        verdict_reasons=outcome.verdict_reasons,
        brief=outcome.brief,
        statuses=outcome.statuses,
        claims=outcome.claims,
        conflicts=[k.model_dump(mode="json") for k in outcome.conflicts],
        scorecard=outcome.scorecard.model_dump(mode="json"),
        gaps=outcome.gaps,
        challenge_note=outcome.challenge_note,
        stopped_because=outcome.stopped_because,
        sources=sources,
        charts=charts,
        files=[*files, "dossier.json"],
    )
    _put(root, "dossier.json", doc.model_dump_json(indent=2), ctx, [])

    blobs = BlobStore(ctx.home.blobs_dir, ctx.home.db_path)
    for sha in {s.sha256 for s in sources}:
        blobs.ref(sha, f"dossier:{root}")  # gc must not reap what a dossier cites
    TopicMemory(ctx.home.db_path).record(title, str(root), today)
    return root


# --- listing ------------------------------------------------------------------------------------


class DossierInfo(BaseModel):
    date: date
    nn: int
    slug: str
    path: Path
    topic: str
    verdict: str
    facts: int
    usd: float
    credits: int
    gap_rate: float
    facts_per_usd: float | None


def list_dossiers(briefs_dir: Path) -> list[DossierInfo]:
    """Every readable dossier under `briefs_dir`, newest first (date, then NN)."""
    found: list[DossierInfo] = []
    for day in sorted(briefs_dir.glob("*-*-*")):
        try:
            when = date.fromisoformat(day.name)
        except ValueError:
            continue
        for folder in sorted(p for p in day.iterdir() if p.is_dir()):
            m = _DATE_DIR.match(folder.name)
            try:
                doc = DossierDoc.model_validate_json(
                    (folder / "dossier.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            sc = doc.scorecard
            found.append(
                DossierInfo(
                    date=when,
                    nn=int(m[1]) if m else 0,
                    slug=folder.name.split("-", 1)[-1],
                    path=folder,
                    topic=doc.topic,
                    verdict=doc.verdict,
                    facts=int(sc.get("facts", 0)),
                    usd=float(sc.get("usd_spent", 0.0)),
                    credits=int(sc.get("credits_spent", 0)),
                    gap_rate=float(sc.get("gap_rate", 0.0)),
                    facts_per_usd=sc.get("facts_per_usd"),
                )
            )
    return sorted(found, key=lambda d: (d.date, d.nn), reverse=True)
