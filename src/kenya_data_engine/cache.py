"""SQLite-backed HTTP cache with per-entry expiry."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel


class CacheEntry(BaseModel):
    key: str
    content: bytes
    content_type: str
    fetched_at: datetime
    final_url: str | None = None  # set by policy fetches: where the redirects ended


class Cache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                "key TEXT PRIMARY KEY, content BLOB, content_type TEXT, "
                "fetched_at TEXT, expires_at TEXT, final_url TEXT)"
            )
            cols = {r[1] for r in conn.execute("PRAGMA table_info(cache)")}
            if "final_url" not in cols:  # databases from before policy-namespaced entries
                conn.execute("ALTER TABLE cache ADD COLUMN final_url TEXT")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def get(self, key: str, now: datetime | None = None) -> CacheEntry | None:
        now = now or datetime.now(UTC)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT content, content_type, fetched_at, expires_at, final_url FROM cache "
                "WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None or datetime.fromisoformat(row[3]) <= now:
            return None
        return CacheEntry(
            key=key,
            content=row[0],
            content_type=row[1],
            fetched_at=datetime.fromisoformat(row[2]),
            final_url=row[4],
        )

    def put(
        self,
        key: str,
        content: bytes,
        content_type: str,
        ttl_hours: float,
        final_url: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        expires = now + timedelta(hours=ttl_hours)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache "
                "(key, content, content_type, fetched_at, expires_at, final_url) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, content, content_type, now.isoformat(), expires.isoformat(), final_url),
            )


class NoCache(Cache):
    """A cache that never hits and never stores: forces live fetches."""

    def __init__(self) -> None:
        pass

    def get(self, key: str, now: datetime | None = None) -> CacheEntry | None:
        return None

    def put(
        self,
        key: str,
        content: bytes,
        content_type: str,
        ttl_hours: float,
        final_url: str | None = None,
    ) -> None:
        return None
