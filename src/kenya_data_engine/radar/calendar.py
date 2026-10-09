"""Calendar adapter: recurring and one-off dated events from calendar.yaml."""

import calendar
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import ConfigError
from kenya_data_engine.models import Signal, signal_id


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def occurrences(entry: dict[str, Any], start: date, end: date) -> list[date]:
    """Dates in [start, end] on which a calendar entry falls."""
    rule = entry.get("rule")
    found: list[date] = []
    if rule == "monthly":
        day = int(entry["day"])
        for y, m in _months(start, end):
            last = calendar.monthrange(y, m)[1]
            found.append(date(y, m, last if day == -1 else min(day, last)))
    elif rule == "annual":
        for y in range(start.year, end.year + 1):
            d = _safe_date(y, int(entry["month"]), int(entry["day"]))
            if d:
                found.append(d)
    elif rule == "date":
        raw = entry["date"]
        d = raw if isinstance(raw, date) else date.fromisoformat(str(raw))
        found.append(d)
    return sorted(d for d in found if start <= d <= end)


def _invalid(label: str, problem: str) -> ConfigError:
    return ConfigError(
        f"calendar entry {label}: {problem}",
        hint="see the packaged calendar.yaml for the format (`engine init --force`)",
    )


def _int_field(entry: dict[str, Any], key: str, label: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(label, f"`{key}` must be a whole number")
    return value


def validate_entry(entry: object, index: int) -> dict[str, Any]:
    """Check one calendar entry, raising a ConfigError that names it."""
    if not isinstance(entry, dict):
        raise _invalid(f"{index}", "must be a mapping with `title` and `rule`")
    title = str(entry.get("title") or "").strip()
    if not title:
        raise _invalid(f"{index}", "missing `title`")
    label = repr(title)
    rule = entry.get("rule")
    if rule == "monthly":
        day = _int_field(entry, "day", label)
        if day != -1 and not 1 <= day <= 28:
            raise _invalid(label, "`day` must be 1-28 or -1 (last day)")
    elif rule == "annual":
        month = _int_field(entry, "month", label)
        day = _int_field(entry, "day", label)
        if _safe_date(2000, month, day) is None:  # 2000 is a leap year, so 29 Feb is allowed
            raise _invalid(label, f"{month}/{day} is not a real month/day")
    elif rule == "date":
        raw = entry.get("date")
        try:
            if not isinstance(raw, date):
                date.fromisoformat(str(raw))
        except ValueError:
            raise _invalid(label, "`date` must be an ISO date like 2026-12-01") from None
    else:
        raise _invalid(label, "`rule` must be monthly, annual or date")
    return entry


class CalendarAdapter:
    name = "calendar"

    def __init__(self, path: Path, lookahead_days: int) -> None:
        self.path = path
        self.lookahead_days = lookahead_days

    def _entries(self) -> list[dict[str, Any]]:
        if self.path.exists():
            text = self.path.read_text(encoding="utf-8")
        else:
            text = (
                resources.files("kenya_data_engine")
                .joinpath("defaults/calendar.yaml")
                .read_text(encoding="utf-8")
            )
        where = str(self.path) if self.path.exists() else "defaults/calendar.yaml"
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {where}", hint=str(exc)) from exc
        if data is None:
            return []
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(data, dict) or ("events" in data and not isinstance(events, list)):
            raise ConfigError(
                f"{where} must contain an `events:` list",
                hint="see the packaged calendar.yaml for the format (`engine init --force`)",
            )
        return [validate_entry(e, i) for i, e in enumerate(events or [], 1)]

    async def fetch(self, ctx: RunContext, since: datetime) -> list[Signal]:
        today = datetime.now(UTC).date()
        end = today + timedelta(days=self.lookahead_days)
        signals: list[Signal] = []
        for entry in self._entries():
            name = str(entry["title"]).strip()
            for d in occurrences(entry, today, end):
                title = f"{name} — {d:%d %b %Y}"
                signals.append(
                    Signal(
                        id=signal_id(None, title),
                        kind="calendar",
                        title=title,
                        source=self.name,
                        url=None,
                        published_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
                        meta={
                            "category": str(entry.get("category", "")),
                            "note": str(entry.get("note", "")),
                        },
                    )
                )
        return signals
