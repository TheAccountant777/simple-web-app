from datetime import UTC, date, datetime

import pytest

from kenya_data_engine.data.periods import (
    NAIROBI,
    epra_cycle,
    fy,
    parse_period,
    today_nairobi,
)


def test_fy_spans_july_to_june():
    p = fy(2025)
    assert (p.type, p.start, p.end, p.label) == (
        "fy",
        date(2025, 7, 1),
        date(2026, 6, 30),
        "FY2025/26",
    )
    assert fy(2099).label == "FY2099/00"


def test_epra_cycle():
    p = epra_cycle(date(2026, 10, 3))
    assert (p.start, p.end, p.label) == (date(2026, 9, 15), date(2026, 10, 14), "EPRA 2026-09-15")
    assert epra_cycle(date(2026, 10, 15)).start == date(2026, 10, 15)
    assert epra_cycle(date(2026, 10, 14)).start == date(2026, 9, 15)
    jan = epra_cycle(date(2026, 1, 3))
    assert (jan.start, jan.end) == (date(2025, 12, 15), date(2026, 1, 14))
    dec = epra_cycle(date(2026, 12, 20))
    assert (dec.start, dec.end) == (date(2026, 12, 15), date(2027, 1, 14))


@pytest.mark.parametrize("text", ["Sep 2026", "September 2026", "2026-09", "2026M09", "Sept 2026"])
def test_month_forms(text):
    p = parse_period(text)
    assert p is not None
    assert (p.type, p.start, p.end, p.label) == (
        "month",
        date(2026, 9, 1),
        date(2026, 9, 30),
        "2026-09",
    )


def test_february_leap_end():
    p = parse_period("Feb 2028")
    assert p is not None and p.end == date(2028, 2, 29)


@pytest.mark.parametrize("text", ["Q3 2026", "2026Q3"])
def test_quarter_forms(text):
    p = parse_period(text)
    assert p is not None
    assert (p.type, p.start, p.end, p.label) == (
        "quarter",
        date(2026, 7, 1),
        date(2026, 9, 30),
        "2026-Q3",
    )


@pytest.mark.parametrize("text", ["FY2025/26", "2025/26", "FY 2025/2026", "fy2025/26"])
def test_fy_forms(text):
    p = parse_period(text)
    assert p is not None and p.type == "fy" and p.label == "FY2025/26"
    assert p.start == date(2025, 7, 1)


def test_year_and_hint():
    y = parse_period("2026")
    assert y is not None and (y.type, y.start, y.end) == (
        "year",
        date(2026, 1, 1),
        date(2026, 12, 31),
    )
    f = parse_period("2025", hint="fy")
    assert f is not None and f.label == "FY2025/26"


def test_dates_and_hints():
    d = parse_period("2026-10-03")
    assert d is not None and d.type == "day" and d.start == d.end == date(2026, 10, 3)
    e = parse_period("2026-10-03", hint="epra_cycle")
    assert e is not None and e.label == "EPRA 2026-09-15"
    w = parse_period("2026-10-03", hint="week")
    assert w is not None and (w.start, w.end, w.label) == (
        date(2026, 9, 28),
        date(2026, 10, 4),
        "2026-W40",
    )
    w2 = parse_period("2026-W40")
    assert w2 == w
    ep = parse_period("EPRA 2026-09-15")
    assert ep is not None and ep.end == date(2026, 10, 14)


@pytest.mark.parametrize(
    "text",
    [
        "garbage",
        "September",
        "",
        "Q3",
        "2026-13",
        "2026-02-30",
        "2025/27",
        "Foo 2026",
        "EPRA 2026-13-01",
        "2026-W99",
    ],
)
def test_unparseable_is_none(text):
    assert parse_period(text) is None


def test_today_nairobi_crosses_midnight():
    assert today_nairobi(datetime(2026, 10, 3, 22, 0, tzinfo=UTC)) == date(2026, 10, 4)
    assert today_nairobi(datetime(2026, 10, 3, 22, 0)) == date(2026, 10, 4)
    assert today_nairobi(datetime(2026, 10, 3, 22, 0, tzinfo=NAIROBI)) == date(2026, 10, 3)
    assert isinstance(today_nairobi(), date)
