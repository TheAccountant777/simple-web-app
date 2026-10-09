"""Content-addressed blob store and the vintaged series store."""

import hashlib
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

from kenya_data_engine.data.models import Observation, Provenance, StoredObservation
from kenya_data_engine.data.periods import Period

_SHA = re.compile(r"[0-9a-f]{64}")


_BLOB_REFS = "CREATE TABLE IF NOT EXISTS blob_refs (sha TEXT, owner TEXT, PRIMARY KEY (sha, owner))"


class BlobStore:
    """Content-addressed files. `db_path` (the engine database) holds `blob_refs`: blobs that
    something other than a stored observation (e.g. a research run's evidence) depends on."""

    def __init__(self, root: Path, db_path: Path | None = None) -> None:
        self.root = root
        self.db_path = db_path
        root.mkdir(parents=True, exist_ok=True)

    def ref(self, sha: str, owner: str) -> None:
        """Record that `owner` (e.g. "evidence:<run id>") needs this blob; gc keeps it."""
        self.path(sha)  # validates the digest
        if self.db_path is None:
            raise ValueError("BlobStore.ref needs a db_path")
        with closing(sqlite3.connect(self.db_path, timeout=30)) as conn, conn:
            conn.execute(_BLOB_REFS)
            conn.execute("INSERT OR IGNORE INTO blob_refs (sha, owner) VALUES (?, ?)", (sha, owner))

    @staticmethod
    def referenced(db_path: Path) -> set[str]:
        """Every sha recorded through `ref`, whatever its owner."""
        with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
            conn.execute(_BLOB_REFS)
            return {r[0] for r in conn.execute("SELECT DISTINCT sha FROM blob_refs")}

    def path(self, sha: str) -> Path:
        if not _SHA.fullmatch(sha):
            raise ValueError(f"not a sha256 hex digest: {sha!r}")
        return self.root / sha[:2] / sha

    def put(self, content: bytes) -> str:
        sha = hashlib.sha256(content).hexdigest()
        target = self.path(sha)
        if target.exists():
            os.utime(target)  # a re-fetched blob is fresh again: gc ages by last use
            return sha
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return sha

    def get(self, sha: str) -> bytes:
        return self.path(sha).read_bytes()

    def prune(self, older_than: timedelta, keep: set[str], *, dry_run: bool = False) -> list[Path]:
        """Delete blobs not in `keep` whose mtime is older than `older_than`.

        Returns the paths removed (with `dry_run`, the paths that would be)."""
        cutoff = time.time() - older_than.total_seconds()
        removed: list[Path] = []
        for f in sorted(self.root.glob("*/*")):
            if f.name.startswith(".tmp-") or f.name in keep or not f.is_file():
                continue
            try:
                if f.stat().st_mtime >= cutoff:
                    continue
                if not dry_run:
                    f.unlink()
            except FileNotFoundError:  # raced with another gc
                continue
            removed.append(f)
        return removed


class AddResult(NamedTuple):
    new: int
    unchanged: int
    revised: int


_COLUMNS = (
    "series, period_label, period_type, start, end, entity, metric, value, unit, vintage, "
    "url, blob, retrieved_at, published, locator, extractor"
)


_REVISED = (
    "EXISTS (SELECT 1 FROM observations p WHERE p.series=o.series AND "
    "p.period_label=o.period_label AND p.entity=o.entity AND p.metric=o.metric "
    "AND p.vintage<o.vintage AND p.value<>o.value)"
)


class SeriesStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS observations ("
                "series TEXT, period_label TEXT, period_type TEXT, start TEXT, end TEXT, "
                "entity TEXT, metric TEXT, value TEXT, unit TEXT, vintage INTEGER, "
                "url TEXT, blob TEXT, retrieved_at TEXT, published TEXT, locator TEXT, "
                "extractor TEXT, "
                "PRIMARY KEY (series, period_label, entity, metric, vintage))"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def add(self, obs: list[Observation]) -> AddResult:
        new = unchanged = revised = 0
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")  # writers serialize; vintages cannot collide
            try:
                new, unchanged, revised = self._add_rows(conn, obs)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        return AddResult(new, unchanged, revised)

    @staticmethod
    def _add_rows(conn: sqlite3.Connection, obs: list[Observation]) -> tuple[int, int, int]:
        new = unchanged = revised = 0
        for o in obs:
            key = (o.series, o.period.label, o.entity, o.metric)
            row = conn.execute(
                "SELECT value, vintage FROM observations WHERE series=? AND period_label=? "
                "AND entity=? AND metric=? ORDER BY vintage DESC LIMIT 1",
                key,
            ).fetchone()
            if row is not None and Decimal(row[0]) == o.value:
                unchanged += 1
                continue
            vintage = 1 if row is None else row[1] + 1
            p = o.provenance
            conn.execute(
                f"INSERT INTO observations ({_COLUMNS}) VALUES ({','.join('?' * 16)})",
                (
                    o.series,
                    o.period.label,
                    o.period.type,
                    o.period.start.isoformat(),
                    o.period.end.isoformat(),
                    o.entity,
                    o.metric,
                    str(o.value),
                    o.unit,
                    vintage,
                    p.url,
                    p.blob_sha256,
                    p.retrieved_at.isoformat(),
                    p.published.isoformat() if p.published else None,
                    p.locator,
                    p.extractor,
                ),
            )
            if row is None:
                new += 1
            else:
                revised += 1
        return new, unchanged, revised

    @staticmethod
    def _to_obs(r: tuple[Any, ...], revised: bool) -> StoredObservation:
        return StoredObservation(
            series=r[0],
            period=Period(
                type=r[2],
                start=date.fromisoformat(r[3]),
                end=date.fromisoformat(r[4]),
                label=r[1],
            ),
            entity=r[5],
            metric=r[6],
            value=Decimal(r[7]),
            unit=r[8],
            vintage=r[9],
            revised=revised,
            provenance=Provenance(
                url=r[10],
                blob_sha256=r[11],
                retrieved_at=datetime.fromisoformat(r[12]),
                published=date.fromisoformat(r[13]) if r[13] else None,
                locator=r[14],
                extractor=r[15],
            ),
        )

    def _rows(self, sql: str, args: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        with closing(self._connect()) as conn:
            return conn.execute(sql, args).fetchall()

    def latest(self, series: str, entity: str | None = None) -> list[StoredObservation]:
        sql = (
            f"SELECT {_COLUMNS}, {_REVISED} FROM observations o WHERE series=? "
            "AND vintage = (SELECT MAX(vintage) FROM observations i WHERE i.series=o.series "
            "AND i.period_label=o.period_label AND i.entity=o.entity AND i.metric=o.metric)"
        )
        args: tuple[Any, ...] = (series,)
        if entity is not None:
            sql += " AND entity=?"
            args += (entity,)
        sql += " ORDER BY start, period_label, entity, metric"
        return [self._to_obs(r, bool(r[16])) for r in self._rows(sql, args)]

    def history(
        self, series: str, period_label: str, entity: str, metric: str
    ) -> list[StoredObservation]:
        rows = self._rows(
            f"SELECT {_COLUMNS}, {_REVISED} FROM observations o WHERE series=? "
            "AND period_label=? AND entity=? AND metric=? ORDER BY vintage",
            (series, period_label, entity, metric),
        )
        return [self._to_obs(r, bool(r[16])) for r in rows]

    def series_keys(self) -> list[str]:
        return [r[0] for r in self._rows("SELECT DISTINCT series FROM observations ORDER BY 1", ())]

    def referenced_blobs(self) -> set[str]:
        return {r[0] for r in self._rows("SELECT DISTINCT blob FROM observations", ())}
