"""Figures for satisfied needs: every number a claim may cite is computed here, by code."""

from collections.abc import Callable
from contextlib import suppress

from pydantic import BaseModel

from kenya_data_engine.data.csvsafe import write_csv
from kenya_data_engine.data.models import StoredObservation
from kenya_data_engine.data.stats import Figure, FigureBook, ref
from kenya_data_engine.data.store import SeriesStore
from kenya_data_engine.research.models import DataNeed, FigureKind, FigureRef, NeedStatus

MAX_ENTITIES = 5
CSV_HEADER = ["series", "entity", "metric", "period", "value", "unit", "source_url", "vintage"]


class FigurePack(BaseModel):
    refs: list[FigureRef]
    markdown: str
    comparisons_csv: str


def _in_range(o: StoredObservation, need: DataNeed) -> bool:
    if need.period_start is not None and o.period.end < need.period_start:
        return False
    return need.period_end is None or o.period.start <= need.period_end


def _entities(rows: list[StoredObservation], need: DataNeed) -> list[str]:
    """At most MAX_ENTITIES, in the need's order; with none named, in order of first appearance."""
    present: dict[str, str] = {}
    for o in rows:
        present.setdefault(o.entity.strip().lower(), o.entity)
    if need.entities:
        wanted = [e.strip().lower() for e in need.entities]
        names = [present[w] for w in dict.fromkeys(wanted) if w in present]
    else:
        names = sorted(present.values())
    return names[:MAX_ENTITIES]


def _one_group(fbook: FigureBook, obs: list[StoredObservation]) -> list[tuple[FigureKind, Figure]]:
    """Latest, change, percentage change and year-on-year for one (series, entity, metric)."""
    latest = obs[-1]
    where = f"{latest.entity} {latest.metric}"
    out: list[tuple[FigureKind, Figure]] = []
    first = fbook.mean([latest], f"{where} {latest.period.label}")
    first.formula = "value"  # one observation: the mean is the value itself
    out.append(("latest", first))
    if len(obs) >= 2:
        prev = obs[-2]
        span = f"{prev.period.label} to {latest.period.label}"
        makers: list[tuple[Callable[..., Figure], FigureKind, str]] = [
            (fbook.change, "change", "change"),
            (fbook.pct_change, "pct_change", "% change"),
        ]
        for make, kind, what in makers:
            try:
                out.append((kind, make(prev, latest, f"{where} {what} {span}")))
            except ValueError:
                continue
    with suppress(ValueError):  # no period a year earlier, or a zero base
        out.append(("yoy", fbook.yoy(obs, f"{where} year-on-year to {latest.period.label}")))
    return out


def figures_for(
    needs: list[DataNeed],
    statuses: list[NeedStatus],
    store: SeriesStore,
    tiers_by_series: dict[str, int],
    generic_series: set[str],
) -> tuple[FigureBook, FigurePack]:
    """Latest value, change, percentage change and year-on-year for each satisfied or partial
    series need. Each status's `figure_ids` is set to the figures made for its need."""
    fbook = FigureBook()
    refs: list[FigureRef] = []
    used: dict[str, StoredObservation] = {}
    by_need = {s.need_id: s for s in statuses}
    for need in needs:
        status = by_need.get(need.id)
        if need.kind != "series" or status is None or status.status == "not_found":
            continue
        status.figure_ids = []
        for key in dict.fromkeys(status.series_keys):
            rows = [o for o in store.latest(key) if _in_range(o, need)]
            for entity in _entities(rows, need):
                for metric in dict.fromkeys(
                    o.metric for o in rows if o.entity.strip().lower() == entity.strip().lower()
                ):
                    obs = sorted(
                        (
                            o
                            for o in rows
                            if o.entity.strip().lower() == entity.strip().lower()
                            and o.metric == metric
                        ),
                        key=lambda o: o.period.start,
                    )
                    obs = [o for o in obs if o.period.type == obs[-1].period.type]
                    lookup = {ref(o): o for o in obs}
                    latest = obs[-1]
                    for kind, fig in _one_group(fbook, obs):
                        used.update({r: lookup[r] for r in fig.inputs})
                        status.figure_ids.append(fig.id)
                        refs.append(
                            FigureRef(
                                label=fig.id,
                                figure_id=fig.id,
                                series=key,
                                entity=latest.entity,
                                period_label=latest.period.label,
                                generic=key in generic_series,
                                tier=tiers_by_series.get(key, 4),
                                published=latest.provenance.published,
                                kind=kind,
                                metric=latest.metric,
                            )
                        )
    rows_out = [
        (o.series, o.entity, o.metric, o.period.label, o.value, o.unit, o.provenance.url, o.vintage)
        for o in sorted(used.values(), key=lambda o: (o.series, o.entity, o.metric, o.period.start))
    ]
    pack = FigurePack(
        refs=refs, markdown=fbook.to_markdown(), comparisons_csv=write_csv(rows_out, CSV_HEADER)
    )
    return fbook, pack
