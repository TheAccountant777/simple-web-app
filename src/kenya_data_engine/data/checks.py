"""Accept-or-quarantine checks on an extracted table. Nothing is ever fixed here."""

from collections import defaultdict
from decimal import Decimal

from kenya_data_engine.data.models import (
    CheckReport,
    Observation,
    SeriesSpec,
    StoredObservation,
)


def _count_latest_period(rows: list[Observation] | list[StoredObservation]) -> int:
    if not rows:
        return 0
    last = max(r.period.start for r in rows)
    return sum(1 for r in rows if r.period.start == last)


def check_observations(
    obs: list[Observation],
    spec: SeriesSpec,
    previous: list[StoredObservation],
    rejects: list[str] | None = None,
) -> CheckReport:
    """`rejects` are cells the adapter could not read as a value; each one is a failure."""
    failures: list[str] = list(rejects or [])
    warnings: list[str] = []
    if not obs and not rejects:
        failures.append("no observations extracted")

    allowed = set(spec.metrics) or {spec.metric}
    seen: dict[tuple[str, str, str], Decimal] = {}
    for o in obs:
        where = f"{o.entity} {o.period.label}"
        if o.metric not in allowed:
            failures.append(f"unexpected metric {o.metric!r} for {where}")
        if o.unit != spec.unit:
            failures.append(f"unit mismatch for {where}: {o.unit!r} != {spec.unit!r}")
        if o.period.type != spec.period_type:
            failures.append(f"period type mismatch for {where}: {o.period.type}")
        if spec.min_value is not None and o.value < spec.min_value:
            failures.append(f"{where} value {o.value} below minimum {spec.min_value}")
        if spec.max_value is not None and o.value > spec.max_value:
            failures.append(f"{where} value {o.value} above maximum {spec.max_value}")
        key = (o.period.label, o.metric, o.entity)
        if key in seen and seen[key] != o.value:
            failures.append(f"duplicate key {where} {o.metric} has conflicting values")
        seen.setdefault(key, o.value)

    present = {o.entity for o in obs}
    missing = [e for e in spec.entities if e not in present]
    if missing:
        failures.append(f"missing expected entities: {', '.join(missing)}")

    prev = [p for p in previous if p.period.type == spec.period_type]
    before, now = _count_latest_period(prev), _count_latest_period(obs)
    if before and now < before:
        failures.append(f"fewer rows than before: {now} < {before}")

    by_period: dict[tuple[str, str], dict[str, Decimal]] = defaultdict(dict)
    for (label, metric, entity), value in seen.items():
        by_period[(label, metric)][entity] = value
    for total, parts in spec.totals:
        for (label, metric), ents in by_period.items():
            if total not in ents or any(p not in ents for p in parts):
                continue
            diff = abs(sum((ents[p] for p in parts), Decimal(0)) - ents[total])
            if diff > spec.total_tolerance:
                failures.append(
                    f"totals mismatch {total} {label} {metric}: components differ by {diff} "
                    f"(tolerance {spec.total_tolerance})"
                )

    if spec.max_step_pct is not None:
        for o in obs:
            earlier = [
                p
                for p in prev
                if p.entity == o.entity
                and p.metric == o.metric
                and p.period.start <= o.period.start
            ]
            if not earlier:
                continue
            base = max(earlier, key=lambda p: p.period.start)
            if base.value == 0:
                continue
            step = abs(o.value - base.value) / abs(base.value) * 100
            if step > spec.max_step_pct:
                warnings.append(
                    f"jump for {o.entity} {o.period.label}: {base.value} -> {o.value} "
                    f"({step:.1f}% > {spec.max_step_pct}%); needs corroboration"
                )

    return CheckReport(
        status="quarantined" if failures else "accepted",
        failures=failures,
        warnings=warnings,
        checked=len(obs),
    )
