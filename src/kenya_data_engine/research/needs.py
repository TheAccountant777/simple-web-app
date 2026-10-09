"""Executing source specs and the code-side gap check (does the data meet the need?).

Nothing here calls an LLM. Specs are fetched under the data fetch policy, extracted with the
locator's column map, checked, and stored; the gap check counts what actually landed.
"""

import hashlib
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from kenya_data_engine.data.adapters.base import policy_fetch
from kenya_data_engine.data.adapters.listing import cell_unit
from kenya_data_engine.data.checks import check_observations
from kenya_data_engine.data.extract import extract_tables
from kenya_data_engine.data.extract.grid import SPAN, Locator, RawTable
from kenya_data_engine.data.models import (
    CheckReport,
    Observation,
    Provenance,
    SeriesSpec,
    StoredObservation,
)
from kenya_data_engine.data.periods import Period, PeriodType, parse_period
from kenya_data_engine.data.registry import fetch_series
from kenya_data_engine.data.store import BlobStore, SeriesStore
from kenya_data_engine.errors import ExtractError
from kenya_data_engine.research.evidence import EvidenceBook
from kenya_data_engine.research.models import DataNeed, DataSourceSpec, NeedStatus
from kenya_data_engine.research.tools import ResearchDeps, robots_blocked, url_allowed
from kenya_data_engine.tools.numbers import is_missing, parse_number

_PERIOD_TYPES: dict[str, PeriodType] = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
    "quarterly": "quarter",
    "annual": "year",
    "cycle": "epra_cycle",
}
_NOTE_FAILURES = 3  # check failures quoted per result in a need's notes


class ExecResult(BaseModel):
    spec: DataSourceSpec
    series_keys: list[str] = []
    evidence_ids: list[str] = []
    points: int = 0
    report: CheckReport | None = None
    generic: bool = False  # extracted by a model-chosen locator, not a registry parser
    error: str | None = None


def _shown(text: str) -> str:
    text = " ".join(text.split())
    return repr(text if len(text) <= 40 else text[:37] + "...")


def _in_range(o: StoredObservation, need: DataNeed) -> bool:
    if need.period_start is not None and o.period.end < need.period_start:
        return False
    return need.period_end is None or o.period.start <= need.period_end


def _count(store: SeriesStore, keys: list[str], need: DataNeed) -> int:
    """Stored observations (latest vintage) in the need's period range, for its entities."""
    wanted = {e.strip().lower() for e in need.entities}
    total = 0
    for key in dict.fromkeys(keys):
        for o in store.latest(key):
            if (not wanted or o.entity.strip().lower() in wanted) and _in_range(o, need):
                total += 1
    return total


# --- generic table extraction -----------------------------------------------------------------


def _build_observations(
    table: RawTable,
    locator: Locator,
    need: DataNeed,
    spec: DataSourceSpec,
    *,
    series: str,
    prov: tuple[str, str, datetime],
) -> tuple[list[Observation], list[str]]:
    columns = {str(k).strip().lower(): str(v) for k, v in locator.columns.items()}
    if not columns:
        raise ExtractError("locator.columns is required to read a table")
    roles = [(i, columns.get(h.strip().lower())) for i, h in enumerate(table.header)]
    entity_col = next((i for i, r in roles if r == "entity"), None)
    period_col = next((i for i, r in roles if r == "period"), None)
    value_cols = [(i, r.split(":", 1)[1]) for i, r in roles if r and r.startswith("value:")]
    if not value_cols:
        raise ExtractError(
            f"none of the mapped columns {sorted(columns)} found in headers {table.header}"
        )
    hint = _PERIOD_TYPES.get(need.frequency)
    fixed = parse_period(spec.expected_period, hint) if spec.expected_period else None
    if period_col is None and fixed is None:
        raise ExtractError("locator.columns has no period column and the spec gives no period")
    canon = {e.strip().lower(): e for e in need.entities}
    default_entity = need.entities[0] if len(need.entities) == 1 else "Kenya"
    url, sha, retrieved = prov
    out: list[Observation] = []
    rejects: list[str] = []

    def where(r: int, c: int) -> str:
        locs = table.cell_locators[r]
        return locs[c] if c < len(locs) else f"r{r}/c{c}"

    for r, row in enumerate(table.rows):
        row = row + [""] * (len(table.header) - len(row))
        raw_entity = row[entity_col].strip() if entity_col is not None else default_entity
        entity = canon.get(raw_entity.lower(), raw_entity)
        period: Period | None = fixed
        if period_col is not None:
            text = row[period_col].strip()
            period = parse_period(text, hint)
            if period is None and text:
                rejects.append(f"{where(r, period_col)}: unparsable period {_shown(text)}")
        if not entity or period is None:
            continue
        for c, metric in value_cols:
            text, loc = row[c].strip(), where(r, c)
            if text and loc.startswith(SPAN):
                rejects.append(
                    f"{loc[len(SPAN) :]}: spanned value {_shown(text)} copied into "
                    f"{entity} {metric}"
                )
                continue
            parsed = parse_number(text)
            if parsed is None:
                if text and not is_missing(text):
                    rejects.append(f"{loc}: unparsable value {_shown(text)}")
                continue
            out.append(
                Observation(
                    series=series,
                    period=period,
                    entity=entity,
                    metric=metric,
                    value=parsed.value,
                    unit=cell_unit(need.unit or "n/a", parsed.unit),
                    provenance=Provenance(
                        url=url,
                        blob_sha256=sha,
                        retrieved_at=retrieved,
                        locator=loc,
                        extractor=table.extractor,
                    ),
                )
            )
    return out, rejects


def _series_spec(series: str, need: DataNeed, obs: list[Observation]) -> SeriesSpec:
    metrics = list(dict.fromkeys(o.metric for o in obs)) or [need.metric or "value"]
    period_type = _PERIOD_TYPES.get(need.frequency) or (obs[0].period.type if obs else "year")
    return SeriesSpec(
        key=series,
        metric=metrics[0],
        metrics=metrics,
        unit=need.unit or (obs[0].unit if obs else "n/a"),
        period_type=period_type,
        entities=list(need.entities),
    )


async def _execute_table(
    spec: DataSourceSpec, need: DataNeed, deps: ResearchDeps, url: str
) -> ExecResult:
    ctx = deps.ctx
    locator = spec.locator or Locator()
    res = await policy_fetch(url, ctx, "item")
    blobs = BlobStore(ctx.home.blobs_dir, ctx.home.db_path)
    sha = blobs.put(res.content)
    blobs.ref(sha, f"evidence:{ctx.run.run_id}")
    tables = await extract_tables(res.content, locator, ctx.config.data)
    if locator.table_index >= len(tables):
        raise ExtractError(f"table {locator.table_index} not found ({len(tables)} tables)")
    host = urlsplit(res.url).hostname or "unknown"
    ident = f"{url}|{locator.model_dump_json()}|{need.metric}"
    series = f"disc:{host}:{hashlib.sha256(ident.encode()).hexdigest()[:8]}"
    obs, rejects = _build_observations(
        tables[locator.table_index],
        locator,
        need,
        spec,
        series=series,
        prov=(ctx.tracer.redact(res.url), sha, res.fetched_at),
    )
    store = SeriesStore(ctx.home.db_path)
    report = check_observations(obs, _series_spec(series, need, obs), store.latest(series), rejects)
    if report.status != "accepted":
        return ExecResult(spec=spec, report=report, generic=True)
    store.add(obs)
    return ExecResult(
        spec=spec,
        series_keys=[series],
        points=_count(store, [series], need),
        report=report,
        generic=True,
    )


async def execute(spec: DataSourceSpec, need: DataNeed, deps: ResearchDeps) -> ExecResult:
    """Fetch and store what one spec points at. Failures come back in `error`, never raised."""
    ctx = deps.ctx
    try:
        if spec.via == "registry":
            key = spec.registry_key
            entry = deps.catalog.get(key) if key else None
            if key is None or entry is None or not entry.enabled:
                return ExecResult(spec=spec, error=f"unknown or disabled registry key {key!r}")
            outcome = await fetch_series(key, ctx)
            store = SeriesStore(ctx.home.db_path)
            return ExecResult(
                spec=spec,
                series_keys=[key] if store.latest(key) else [],
                points=_count(store, [key], need),
                report=outcome.report,
                error=outcome.error,
            )
        url = spec.url
        if not url or not url_allowed(deps, url):
            return ExecResult(spec=spec, error="unknown url — not returned by any tool")
        if await robots_blocked(deps, url):
            return ExecResult(spec=spec, error="disallowed by robots.txt")
        if spec.via == "page_text":
            ev = await deps.book.add_url(url, ctx, kind="page")
            return ExecResult(spec=spec, evidence_ids=[ev.id])
        return await _execute_table(spec, need, deps, url)
    except Exception as exc:
        return ExecResult(
            spec=spec,
            generic=spec.via in ("file", "html_table"),
            error=ctx.tracer.redact(str(exc) or type(exc).__name__),
        )


# --- gap check ----------------------------------------------------------------------------------


def need_status(
    need: DataNeed,
    results: list[ExecResult],
    store: SeriesStore,
    book: EvidenceBook,
    round_no: int,
) -> NeedStatus:
    """Satisfied, partial or not found, counted from what is stored, never from a model."""
    keys = list(dict.fromkeys(k for r in results for k in r.series_keys))
    evidence_ids = list(dict.fromkeys(e for r in results for e in r.evidence_ids))
    notes: list[str] = []
    for r in results:
        label = r.spec.url or r.spec.registry_key or r.spec.via
        if r.error:
            notes.append(f"{label}: {r.error}")
        if r.report is not None and r.report.status == "quarantined":
            fails = "; ".join(r.report.failures[:_NOTE_FAILURES])
            notes.append(f"{label}: quarantined: {fails}")
    status: Literal["satisfied", "partial", "not_found"]
    if need.kind == "series":
        points = _count(store, keys, need)
        if points >= need.min_points:
            status = "satisfied"
        elif points > 0:
            status = "partial"
        else:
            status = "not_found"
    else:
        tiers = [ev.tier for i in evidence_ids if (ev := book.get(i)) is not None]
        points = len(tiers)
        if any(t <= 2 for t in tiers):
            status = "satisfied"
        elif tiers:
            status = "partial"
            notes.append("only tier 3-4 sources found")
        else:
            status = "not_found"
    return NeedStatus(
        need_id=need.id,
        status=status,
        points=points,
        evidence_ids=evidence_ids,
        series_keys=keys,
        notes=notes,
        round=round_no,
    )


__all__ = ["ExecResult", "execute", "need_status"]
