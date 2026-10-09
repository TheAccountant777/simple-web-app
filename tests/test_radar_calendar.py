from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from kenya_data_engine.radar.calendar import CalendarAdapter, occurrences

T0 = datetime(2000, 1, 1, tzinfo=UTC)


def test_occurrences_monthly_last_day():
    got = occurrences({"rule": "monthly", "day": -1}, date(2026, 2, 1), date(2026, 3, 5))
    assert got == [date(2026, 2, 28)]


def test_occurrences_monthly_day_spans_months():
    got = occurrences({"rule": "monthly", "day": 14}, date(2026, 10, 15), date(2026, 12, 14))
    assert got == [date(2026, 11, 14), date(2026, 12, 14)]


def test_occurrences_annual_and_date():
    annual = {"rule": "annual", "month": 6, "day": 12}
    assert occurrences(annual, date(2026, 6, 1), date(2026, 6, 30)) == [date(2026, 6, 12)]
    assert occurrences(annual, date(2026, 7, 1), date(2026, 8, 1)) == []
    one = {"rule": "date", "date": date(2026, 12, 1)}
    assert occurrences(one, date(2026, 11, 20), date(2026, 12, 10)) == [date(2026, 12, 1)]
    assert occurrences(one, date(2026, 1, 1), date(2026, 2, 1)) == []
    assert occurrences(
        {"rule": "date", "date": "2026-12-01"}, date(2026, 11, 20), date(2026, 12, 10)
    )


def test_occurrences_across_year_boundary_and_unknown_rule():
    annual = {"rule": "annual", "month": 1, "day": 1}
    assert occurrences(annual, date(2026, 12, 20), date(2027, 1, 10)) == [date(2027, 1, 1)]
    assert occurrences({"rule": "weird"}, date(2026, 1, 1), date(2026, 2, 1)) == []


async def test_calendar_adapter_emits_signals(tmp_path: Path, ctx, monkeypatch):
    path = tmp_path / "cal.yaml"
    today = datetime.now(UTC).date()
    path.write_text(
        "events:\n"
        "  - {title: Soon, rule: date, date: " + today.isoformat() + ", category: economy,"
        " note: check}\n"
        "  - {title: Far, rule: date, date: 2999-01-01, category: law}\n"
    )
    sigs = await CalendarAdapter(path, 21).fetch(ctx, datetime.now(UTC))
    assert len(sigs) == 1
    s = sigs[0]
    assert s.kind == "calendar" and s.url is None and s.source == "calendar"
    assert s.title == f"Soon — {today:%d %b %Y}"
    assert s.published_at == datetime(today.year, today.month, today.day, tzinfo=UTC)
    assert s.meta == {"category": "economy", "note": "check"}


async def test_calendar_missing_file_uses_packaged_defaults(tmp_path, ctx):
    sigs = await CalendarAdapter(tmp_path / "nope.yaml", 400).fetch(ctx, T0)
    assert any("KNBS CPI release" in s.title for s in sigs)
    assert all(s.meta["note"] is not None for s in sigs)


@pytest.mark.parametrize("content", ["", "events: nope\n", "- a\n"])
async def test_calendar_bad_file_yields_nothing(tmp_path, ctx, content):
    p = tmp_path / "c.yaml"
    p.write_text(content)
    assert await CalendarAdapter(p, 21).fetch(ctx, T0) == []
