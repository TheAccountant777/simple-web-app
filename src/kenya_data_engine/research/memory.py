"""Source memory (which sources worked for which need) and topic memory (what we covered).

Both live in the engine database. Only code writes them: the orchestrator records a source
after its data passed the checks, and a topic after its dossier is written.
"""

import re
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from kenya_data_engine.research.models import DataNeed, DataSourceSpec

FRESH_DAYS = 60  # a remembered source not verified within this many days is not offered
JACCARD_MIN = 0.5
_WORD = re.compile(r"[a-z0-9]+")
_MIN_WORD = 3


def need_signature(need: DataNeed) -> str:
    """Lowercase `metric|sorted entities|unit|frequency`; a fact uses its question as the metric."""
    metric = need.metric or need.question
    entities = ",".join(sorted(e.strip().lower() for e in need.entities))
    parts = (metric.strip(), entities, need.unit or "", need.frequency)
    return "|".join(p.lower() for p in parts)


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) >= _MIN_WORD}


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path, timeout=30)


class SourceMemory:
    """Satisfies `research.tools.SourceMemoryLike`."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with closing(_connect(db_path)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS source_memory ("
                "signature TEXT, spec_json TEXT, successes INTEGER DEFAULT 0, "
                "failures INTEGER DEFAULT 0, last_verified TEXT, "
                "PRIMARY KEY (signature, spec_json))"
            )

    @staticmethod
    def _dump(spec: DataSourceSpec) -> str:
        # the need id belongs to one dossier; the memory is keyed by the need's signature
        return spec.model_copy(update={"need": ""}).model_dump_json()

    def lookup(self, need: DataNeed, today: date) -> list[DataSourceSpec]:
        """Specs that worked more often than they failed and were verified in the last 60 days."""
        with closing(_connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT spec_json, last_verified FROM source_memory "
                "WHERE signature=? AND successes>failures "
                "ORDER BY successes-failures DESC, last_verified DESC, spec_json",
                (need_signature(need),),
            ).fetchall()
        out: list[DataSourceSpec] = []
        for spec_json, verified in rows:
            if verified is None or (today - date.fromisoformat(verified)).days > FRESH_DAYS:
                continue
            spec = DataSourceSpec.model_validate_json(spec_json)
            out.append(spec.model_copy(update={"need": need.id}))
        return out

    def search(self, query: str) -> list[tuple[str, DataSourceSpec]]:
        """(signature, spec) for good entries sharing words with the query, best overlap first."""
        words = _words(query)
        if not words:
            return []
        with closing(_connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT signature, spec_json FROM source_memory WHERE successes>failures"
            ).fetchall()
        scored = []
        for signature, spec_json in rows:
            overlap = len(words & _words(signature))
            if overlap:
                scored.append((overlap, signature, spec_json))
        scored.sort(key=lambda s: (-s[0], s[1], s[2]))
        return [(sig, DataSourceSpec.model_validate_json(js)) for _, sig, js in scored]

    def record(self, need: DataNeed, spec: DataSourceSpec, ok: bool, today: date) -> None:
        sig, js = need_signature(need), self._dump(spec)
        col = "successes" if ok else "failures"
        with closing(_connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO source_memory (signature, spec_json, successes, failures) "
                "VALUES (?, ?, 0, 0)",
                (sig, js),
            )
            conn.execute(
                f"UPDATE source_memory SET {col}={col}+1 WHERE signature=? AND spec_json=?",
                (sig, js),
            )
            if ok:
                conn.execute(
                    "UPDATE source_memory SET last_verified=? WHERE signature=? AND spec_json=?",
                    (today.isoformat(), sig, js),
                )

    def entries(self) -> list[dict[str, Any]]:
        with closing(_connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT signature, spec_json, successes, failures, last_verified "
                "FROM source_memory ORDER BY signature, spec_json"
            ).fetchall()
        return [
            {
                "signature": sig,
                "spec": DataSourceSpec.model_validate_json(js).model_dump(mode="json"),
                "successes": s,
                "failures": f,
                "last_verified": lv,
            }
            for sig, js, s, f, lv in rows
        ]

    def forget(self, signature: str) -> int:
        with closing(_connect(self.db_path)) as conn, conn:
            return conn.execute(
                "DELETE FROM source_memory WHERE signature=?", (signature,)
            ).rowcount


class TopicMemory:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with closing(_connect(db_path)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS topic_memory ("
                "title TEXT, tokens TEXT, dossier_path TEXT, created TEXT)"
            )

    def record(self, title: str, path: str, when: date) -> None:
        tokens = " ".join(sorted(_words(title)))
        with closing(_connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO topic_memory (title, tokens, dossier_path, created) "
                "VALUES (?, ?, ?, ?)",
                (title, tokens, path, when.isoformat()),
            )

    def last_covered(self, title: str, today: date) -> int | None:
        """Days since the most recent covered topic whose title overlaps (Jaccard >= 0.5)."""
        mine = _words(title)
        if not mine:
            return None
        with closing(_connect(self.db_path)) as conn:
            rows = conn.execute("SELECT tokens, created FROM topic_memory").fetchall()
        best: int | None = None
        for tokens, created in rows:
            theirs = set(tokens.split())
            if not theirs or len(mine & theirs) / len(mine | theirs) < JACCARD_MIN:
                continue
            days = max(0, (today - date.fromisoformat(created)).days)
            best = days if best is None else min(best, days)
        return best

    def entries(self) -> list[dict[str, Any]]:
        with closing(_connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT title, dossier_path, created FROM topic_memory "
                "ORDER BY created DESC, rowid DESC"
            ).fetchall()
        return [{"title": t, "dossier_path": p, "created": c} for t, p, c in rows]


def code_novelty(days: int | None, novelty_days: list[int]) -> int | None:
    """Novelty ceiling from recency: `<= 14` days gives 1, `<= 30` gives 3, older gives None.

    `novelty_days` lists the band limits in order; band i caps novelty at 1, 3, ... (step 2).
    """
    if days is None:
        return None
    for i, limit in enumerate(sorted(novelty_days)):
        if days <= limit:
            return 1 + 2 * i
    return None
