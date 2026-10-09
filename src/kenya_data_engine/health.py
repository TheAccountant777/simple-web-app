"""Per-source health, kept in the engine database so auditors can see drift over time."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


class SourceHealth(BaseModel):
    name: str
    last_ok_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    last_signal_count: int | None
    updated_at: datetime


class HealthStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS source_health ("
                "name TEXT PRIMARY KEY, last_ok_at TEXT, last_error TEXT, "
                "consecutive_failures INTEGER NOT NULL DEFAULT 0, "
                "last_signal_count INTEGER, updated_at TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def record_ok(self, name: str, signal_count: int) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO source_health VALUES (?, ?, NULL, 0, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET last_ok_at = excluded.last_ok_at, "
                "last_error = NULL, consecutive_failures = 0, "
                "last_signal_count = excluded.last_signal_count, updated_at = excluded.updated_at",
                (name, now, signal_count, now),
            )

    def record_failure(self, name: str, error: str) -> int:
        """Record a failed fetch and return the new consecutive-failure count."""
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO source_health VALUES (?, NULL, ?, 1, NULL, ?) "
                "ON CONFLICT(name) DO UPDATE SET last_error = excluded.last_error, "
                "consecutive_failures = consecutive_failures + 1, "
                "updated_at = excluded.updated_at",
                (name, error, now),
            )
            row = conn.execute(
                "SELECT consecutive_failures FROM source_health WHERE name = ?", (name,)
            ).fetchone()
        return int(row[0])

    def all(self) -> dict[str, SourceHealth]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT name, last_ok_at, last_error, consecutive_failures, "
                "last_signal_count, updated_at FROM source_health"
            ).fetchall()
        return {
            r[0]: SourceHealth(
                name=r[0],
                last_ok_at=r[1],
                last_error=r[2],
                consecutive_failures=r[3],
                last_signal_count=r[4],
                updated_at=r[5],
            )
            for r in rows
        }


def failing_label(count: int) -> str:
    return f"failing ×{count}"  # noqa: RUF001
