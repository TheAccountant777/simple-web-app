from datetime import UTC, datetime
from decimal import Decimal

import pytest

from kenya_data_engine.data.models import Provenance, StoredObservation
from kenya_data_engine.data.periods import Period, fy, month
from kenya_data_engine.data.stats import FigureBook

PROV = Provenance(
    url="https://x/y",
    blob_sha256="a" * 64,
    retrieved_at=datetime(2026, 10, 1, tzinfo=UTC),
    published=None,
    locator="t0/r1/c1",
    extractor="csv@stdlib",
)


def _s(
    value: str,
    period: Period | None = None,
    unit: str = "KES/L",
    series: str = "pump",
    metric: str = "super",
) -> StoredObservation:
    return StoredObservation(
        series=series,
        period=period or month(2026, 9),
        entity="Nairobi",
        metric=metric,
        value=Decimal(value),
        unit=unit,
        provenance=PROV,
        vintage=1,
        revised=False,
    )


def test_change_exact_with_log() -> None:
    book = FigureBook()
    f = book.change(_s("180.66", month(2026, 8)), _s("184.16"), "Super change")
    assert f.value == Decimal("3.50")
    assert f.id == "F1" and f.unit == "KES/L" and f.formula == "(b - a)"
    assert f.inputs == ["pump|2026-08|Nairobi|super", "pump|2026-09|Nairobi|super"]
    assert book.figures == [f]


def test_pct_change_exact() -> None:
    f = FigureBook().pct_change(_s("200", month(2026, 8)), _s("210"), "x")
    assert f.value == Decimal("5") and f.unit == "pct"
    with pytest.raises(ValueError, match="zero"):
        FigureBook().pct_change(_s("0"), _s("1"), "x")


def test_yoy_across_months() -> None:
    obs = [_s("100", month(2025, 9)), _s("110", month(2026, 9)), _s("105", month(2026, 8))]
    f = FigureBook().yoy(obs, "YoY")
    assert f.value == Decimal("10") and f.unit == "pct"
    assert f.inputs[0].split("|")[1] == "2025-09"


def test_yoy_fy_and_missing_year() -> None:
    f = FigureBook().yoy([_s("50", fy(2024)), _s("55", fy(2025))], "FY")
    assert f.value == Decimal("10")
    with pytest.raises(ValueError, match="year before"):
        FigureBook().yoy([_s("1", month(2026, 9)), _s("1", month(2026, 8))], "x")
    with pytest.raises(ValueError):
        FigureBook().yoy([], "x")
    with pytest.raises(ValueError, match="one series"):
        FigureBook().yoy([_s("1", month(2025, 9)), _s("1", metric="diesel")], "x")


def test_mean() -> None:
    f = FigureBook().mean([_s("10"), _s("11"), _s("12.5", month(2026, 8))], "avg")
    assert f.value == Decimal("33.5") / 3
    assert len(f.inputs) == 3
    with pytest.raises(ValueError):
        FigureBook().mean([], "x")


def test_real_with_explicit_cpi() -> None:
    nominal = _s("100", month(2020, 1), unit="KES")
    then = _s("100", month(2020, 1), unit="index", series="cpi")
    now = _s("125", month(2026, 9), unit="index", series="cpi")
    f = FigureBook().real(nominal, then, now, "Real")
    assert f.value == Decimal("125") and f.unit == "KES"
    assert "2026-09" in f.formula and len(f.inputs) == 3
    with pytest.raises(ValueError, match="mixed units"):
        FigureBook().real(nominal, then, _s("1", unit="other", series="cpi"), "x")


def test_units_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="mixed units"):
        FigureBook().change(_s("1"), _s("2", unit="KES/kg"), "x")
    with pytest.raises(ValueError, match="mixed units"):
        FigureBook().mean([_s("1"), _s("2", unit="KES/kg")], "x")


def test_to_markdown() -> None:
    book = FigureBook()
    book.change(_s("180.66", month(2026, 8)), _s("184.16"), "Super | change")
    book.pct_change(_s("3", month(2026, 8)), _s("4"), "Pct")
    md = book.to_markdown()
    assert "F1" in md and "(b - a)" in md and "F2" in md
    assert "| 3.50 |" in md and "| 33.3 |" in md
    assert "Super \\| change" in md
