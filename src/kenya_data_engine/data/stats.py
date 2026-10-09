"""Figures: every number in a brief is computed here, in Decimal, with its formula and inputs."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from pydantic import BaseModel

from kenya_data_engine.data.models import StoredObservation

PRECISION = 28


class Figure(BaseModel):
    id: str  # assigned by FigureBook: "F1", "F2", ...
    label: str
    value: Decimal
    unit: str
    formula: str  # e.g. "(b - a)"
    inputs: list[str]  # "<series>|<period>|<entity>|<metric>"


def ref(o: StoredObservation) -> str:
    return f"{o.series}|{o.period.label}|{o.entity}|{o.metric}"


def _same_unit(*obs: StoredObservation) -> str:
    units = {o.unit for o in obs}
    if len(units) != 1:
        raise ValueError(f"mixed units: {sorted(units)}")
    return obs[0].unit


def _years_back(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year - 1, day=28)


def _quantize(value: Decimal, places: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


class FigureBook:
    def __init__(self) -> None:
        self.figures: list[Figure] = []

    def _add(
        self, label: str, value: Decimal, unit: str, formula: str, inputs: list[StoredObservation]
    ) -> Figure:
        fig = Figure(
            id=f"F{len(self.figures) + 1}",
            label=label,
            value=value,
            unit=unit,
            formula=formula,
            inputs=[ref(o) for o in inputs],
        )
        self.figures.append(fig)
        return fig

    def change(self, a: StoredObservation, b: StoredObservation, label: str) -> Figure:
        unit = _same_unit(a, b)
        with localcontext() as ctx:
            ctx.prec = PRECISION
            value = b.value - a.value
        return self._add(label, value, unit, "(b - a)", [a, b])

    def pct_change(self, a: StoredObservation, b: StoredObservation, label: str) -> Figure:
        _same_unit(a, b)
        if a.value == 0:
            raise ValueError("percentage change from zero is undefined")
        with localcontext() as ctx:
            ctx.prec = PRECISION
            value = (b.value - a.value) / a.value * 100
        return self._add(label, value, "pct", "((b - a) / a) * 100", [a, b])

    def yoy(self, obs: list[StoredObservation], label: str) -> Figure:
        """Latest period against the same period a year earlier, as a percentage change."""
        if not obs:
            raise ValueError("yoy needs observations")
        if len({(o.series, o.entity, o.metric) for o in obs}) != 1:
            raise ValueError("yoy needs one series, entity and metric")
        latest = max(obs, key=lambda o: o.period.start)
        want = _years_back(latest.period.start)
        earlier = next(
            (o for o in obs if o.period.type == latest.period.type and o.period.start == want),
            None,
        )
        if earlier is None:
            raise ValueError(f"no period a year before {latest.period.label}")
        _same_unit(earlier, latest)
        if earlier.value == 0:
            raise ValueError("percentage change from zero is undefined")
        with localcontext() as ctx:
            ctx.prec = PRECISION
            value = (latest.value - earlier.value) / earlier.value * 100
        return self._add(label, value, "pct", "((b - a) / a) * 100", [earlier, latest])

    def mean(self, obs: list[StoredObservation], label: str) -> Figure:
        if not obs:
            raise ValueError("mean needs observations")
        unit = _same_unit(*obs)
        with localcontext() as ctx:
            ctx.prec = PRECISION
            value = sum((o.value for o in obs), Decimal(0)) / len(obs)
        return self._add(label, value, unit, f"sum(x) / {len(obs)}", obs)

    def real(
        self,
        nominal: StoredObservation,
        cpi_then: StoredObservation,
        cpi_now: StoredObservation,
        label: str,
    ) -> Figure:
        """Nominal value restated in the prices of `cpi_now`'s period."""
        _same_unit(cpi_then, cpi_now)
        if cpi_then.value == 0:
            raise ValueError("CPI of zero is undefined")
        with localcontext() as ctx:
            ctx.prec = PRECISION
            value = nominal.value * cpi_now.value / cpi_then.value
        formula = (
            f"nominal * cpi_now / cpi_then "
            f"(in {cpi_now.period.label} prices; cpi_then base {cpi_then.period.label})"
        )
        return self._add(label, value, nominal.unit, formula, [nominal, cpi_then, cpi_now])

    def to_markdown(self) -> str:
        def cell(text: str) -> str:
            return text.replace("|", "\\|")

        lines = [
            "| id | label | value | unit | formula | inputs |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for f in self.figures:
            shown = _quantize(f.value, 1 if f.unit == "pct" else 2)
            lines.append(
                f"| {f.id} | {cell(f.label)} | {shown} | {cell(f.unit)} | {cell(f.formula)} "
                f"| {cell('; '.join(f.inputs))} |"
            )
        return "\n".join(lines) + "\n"
