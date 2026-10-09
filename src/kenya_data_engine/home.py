"""Engine home directory layout."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EngineHome:
    root: Path

    @property
    def config_path(self) -> Path:
        return self.root / "config.yaml"

    @property
    def calendar_path(self) -> Path:
        return self.root / "calendar.yaml"

    @property
    def sources_path(self) -> Path:
        return self.root / "sources.yaml"

    @property
    def certs_dir(self) -> Path:
        return self.root / "certs"

    @property
    def env_path(self) -> Path:
        return self.root / ".env"

    @property
    def db_path(self) -> Path:
        return self.root / "engine.db"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def briefs_dir(self) -> Path:
        return self.root / "briefs"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(exist_ok=True)
        self.briefs_dir.mkdir(exist_ok=True)

    @classmethod
    def resolve(cls, explicit: Path | None = None) -> "EngineHome":
        if explicit is not None:
            return cls(explicit)
        env = os.environ.get("ENGINE_HOME")
        if env:
            return cls(Path(env))
        return cls(Path.home() / ".kenya-data-engine")
