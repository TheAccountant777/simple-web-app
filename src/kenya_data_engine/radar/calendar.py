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
        return [e for e in events or [] if isinstance(e, dict)]

    async def fetch(self, ctx: RunContext, since: datetime) -> list[Signal]:
        today = datetime.now(UTC).date()
        end = today + timedelta(days=self.lookahead_days)
        signals: list[Signal] = []
        for entry in self._entries():
            name = str(entry.get("title", "")).strip()
            if not name:
                continue
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
