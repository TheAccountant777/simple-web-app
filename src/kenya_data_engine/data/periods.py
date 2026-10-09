"""Period types, including the Kenyan fiscal year (Jul-Jun) and the EPRA price cycle (15th-14th).

Parsing never fills a missing part from today's date: no year means `None`.
"""

import calendar
import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

PeriodType = Literal["day", "week", "month", "quarter", "year", "fy", "epra_cycle"]
NAIROBI = ZoneInfo("Africa/Nairobi")


class Period(BaseModel, frozen=True):
    type: PeriodType
    start: date
    end: date
    label: str  # canonical, e.g. "2026-09", "FY2025/26", "EPRA 2026-09-15"


def today_nairobi(now: datetime | None = None) -> date:
    """Today's date in Nairobi (UTC+3); a naive `now` is taken as UTC."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(NAIROBI).date()


def day(d: date) -> Period:
    return Period(type="day", start=d, end=d, label=d.isoformat())


def week(d: date) -> Period:
    """ISO week (Monday to Sunday) containing `d`."""
    start = d - timedelta(days=d.weekday())
    iso = d.isocalendar()
    return Period(
        type="week", start=start, end=start + timedelta(days=6), label=f"{iso[0]}-W{iso[1]:02d}"
    )


def month(year: int, m: int) -> Period:
    last = calendar.monthrange(year, m)[1]
    return Period(
        type="month", start=date(year, m, 1), end=date(year, m, last), label=f"{year}-{m:02d}"
    )


def quarter(year: int, q: int) -> Period:
    first = 3 * (q - 1) + 1
    return Period(
        type="quarter",
        start=date(year, first, 1),
        end=month(year, first + 2).end,
        label=f"{year}-Q{q}",
    )


def year(y: int) -> Period:
    return Period(type="year", start=date(y, 1, 1), end=date(y, 12, 31), label=str(y))


def fy(year_start: int) -> Period:
    """FY2025/26 runs 2025-07-01 to 2026-06-30."""
    return Period(
        type="fy",
        start=date(year_start, 7, 1),
        end=date(year_start + 1, 6, 30),
        label=f"FY{year_start}/{(year_start + 1) % 100:02d}",
    )


def epra_cycle(on: date) -> Period:
    """The EPRA pricing cycle containing `on`: the 15th to the 14th of the next month."""
    if on.day >= 15:
        start = date(on.year, on.month, 15)
    else:
        start = date(on.year - 1, 12, 15) if on.month == 1 else date(on.year, on.month - 1, 15)
    nxt = (
        date(start.year + 1, 1, 14) if start.month == 12 else date(start.year, start.month + 1, 14)
    )
    return Period(type="epra_cycle", start=start, end=nxt, label=f"EPRA {start.isoformat()}")


_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS |= {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}
_MONTHS["sept"] = 9

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_EPRA = re.compile(r"EPRA\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_ISO_MONTH = re.compile(r"(\d{4})-(\d{2})")
_M_FORM = re.compile(r"(\d{4})M(\d{2})", re.IGNORECASE)
_NAME_MONTH = re.compile(r"([A-Za-z]{3,9})\.?,?\s+(\d{4})")
_QUARTER = re.compile(r"(?:Q([1-4])[\s-]*(\d{4})|(\d{4})[\s-]*Q([1-4]))", re.IGNORECASE)
_WEEK = re.compile(r"(\d{4})-?W(\d{2})", re.IGNORECASE)
_FY = re.compile(r"(?:FY\s*)?(\d{4})\s*/\s*(\d{4}|\d{2})", re.IGNORECASE)
_YEAR = re.compile(r"\d{4}")


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _from_date(d: date, hint: PeriodType | None) -> Period:
    if hint == "epra_cycle":
        return epra_cycle(d)
    if hint == "week":
        return week(d)
    return day(d)


def _year_ok(y: int) -> bool:
    return 1 <= y <= 9998  # fy() and month ends need y + 1 to be representable


def parse_period(text: str, hint: PeriodType | None = None) -> Period | None:
    """Parse a period label; `hint` only disambiguates (a bare year as FY, a date as cycle)."""
    try:
        return _parse(text, hint)
    except (ValueError, OverflowError):  # out-of-range years at the edges of `date`
        return None


def _parse(text: str, hint: PeriodType | None) -> Period | None:
    s = text.strip()
    if m := _EPRA.fullmatch(s):
        d = _safe_date(*(int(p) for p in m[1].split("-")))
        return epra_cycle(d) if d else None
    if m := _ISO_DATE.fullmatch(s):
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        return _from_date(d, hint) if d else None
    if m := _WEEK.fullmatch(s):
        try:
            return week(date.fromisocalendar(int(m[1]), int(m[2]), 1))
        except ValueError:
            return None
    if m := (_ISO_MONTH.fullmatch(s) or _M_FORM.fullmatch(s)):
        return month(int(m[1]), int(m[2])) if 1 <= int(m[2]) <= 12 and _year_ok(int(m[1])) else None
    if m := _QUARTER.fullmatch(s):
        y = int(m[2] or m[3])
        return quarter(y, int(m[1] or m[4])) if _year_ok(y) else None
    if m := _FY.fullmatch(s):
        start, end = int(m[1]), m[2]
        end_full = int(end) if len(end) == 4 else (start // 100) * 100 + int(end)
        if len(end) == 2 and end_full < start:
            end_full += 100  # century wrap: 2099/00
        return fy(start) if end_full == start + 1 and _year_ok(start) else None
    if m := _NAME_MONTH.fullmatch(s):
        mon = _MONTHS.get(m[1].lower())
        return month(int(m[2]), mon) if mon and _year_ok(int(m[2])) else None
    if _YEAR.fullmatch(s):
        if not _year_ok(int(s)):
            return None
        return fy(int(s)) if hint == "fy" else year(int(s))
    return None
