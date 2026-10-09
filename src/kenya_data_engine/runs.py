"""Run directories and atomic JSON artifacts."""

import re
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from kenya_data_engine.errors import EngineError

T = TypeVar("T", bound=BaseModel)

_RUN_ID = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{4})(?:-(\d+))?$")


class RunHandle:
    def __init__(self, run_id: str, dir: Path) -> None:
        self.run_id = run_id
        self.dir = dir

    @property
    def trace_path(self) -> Path:
        return self.dir / "trace.jsonl"

    def write(self, name: str, model: BaseModel) -> Path:
        """Atomically write `<dir>/<name>.json` (write .tmp, then rename)."""
        path = self.dir / f"{name}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(model.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def read(self, name: str, type_: type[T]) -> T | None:
        """Return the artifact, or None if it is missing, truncated or invalid."""
        path = self.dir / f"{name}.json"
        try:
            return type_.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError):
            return None


class RunStore:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir

    def new_run(self, now: datetime) -> RunHandle:
        base = now.strftime("%Y-%m-%d-%H%M")
        run_id, n = base, 1
        while (self.runs_dir / run_id).exists():
            n += 1
            run_id = f"{base}-{n}"
        path = self.runs_dir / run_id
        path.mkdir(parents=True)
        return RunHandle(run_id, path)

    def open(self, run_id: str) -> RunHandle:
        path = self.runs_dir / run_id
        if not path.is_dir():
            raise EngineError(f"run not found: {run_id}", hint="list runs with `engine runs`")
        return RunHandle(run_id, path)

    def list(self) -> list[str]:
        """Run ids, newest first."""
        found: list[tuple[str, int, str]] = []
        if self.runs_dir.is_dir():
            for p in self.runs_dir.iterdir():
                m = _RUN_ID.match(p.name)
                if p.is_dir() and m:
                    found.append((m.group(1), int(m.group(2) or 1), p.name))
        return [name for _, _, name in sorted(found, reverse=True)]

    def latest(self) -> RunHandle | None:
        ids = self.list()
        return self.open(ids[0]) if ids else None
