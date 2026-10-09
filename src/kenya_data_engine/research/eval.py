"""Acceptance scenarios: each runs a topic through the research pipeline and is judged in code."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kenya_data_engine.errors import ConfigError
from kenya_data_engine.home import EngineHome
from kenya_data_engine.research.models import Scorecard


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    topic: str
    expect_verdict: list[str] = Field(default_factory=list)
    min_facts: int | None = None
    max_usd: float | None = None
    max_credits: int | None = None


def _read(text: str, where: str) -> dict[str, dict[str, object]]:
    try:
        data = yaml.safe_load(text) or {}
        scenarios = data.get("scenarios", {})
        if not isinstance(scenarios, dict):
            raise ValueError("`scenarios` must be a mapping of name to scenario")
        return {str(k): dict(v) for k, v in scenarios.items()}
    except (yaml.YAMLError, ValueError, TypeError, AttributeError) as exc:
        raise ConfigError(
            f"{where} is not valid: {exc}", hint="compare with the default eval.yaml"
        ) from exc


def load_scenarios(home: EngineHome) -> list[Scenario]:
    """Packaged scenarios, with `<home>/eval.yaml` merged over them by name."""
    packaged = resources.files("kenya_data_engine").joinpath("defaults/eval.yaml")
    merged = _read(packaged.read_text(encoding="utf-8"), "default eval.yaml")
    user = home.root / "eval.yaml"
    if user.is_file():
        for name, spec in _read(user.read_text(encoding="utf-8"), str(user)).items():
            merged[name] = {**merged.get(name, {}), **spec}
    try:
        return [Scenario(**{**spec, "name": name}) for name, spec in merged.items()]
    except ValidationError as exc:
        raise ConfigError(f"eval scenario invalid: {exc.errors()[0]['msg']}") from exc


def judge(scenario: Scenario, verdict: str, sc: Scorecard) -> list[str]:
    """What the run missed: an empty list means the scenario passed."""
    failures: list[str] = []
    if scenario.expect_verdict and verdict not in scenario.expect_verdict:
        failures.append(f"verdict {verdict} not in {', '.join(scenario.expect_verdict)}")
    if scenario.min_facts is not None and sc.facts < scenario.min_facts:
        failures.append(f"{sc.facts} facts, wanted at least {scenario.min_facts}")
    if scenario.max_usd is not None and sc.usd_spent > scenario.max_usd:
        failures.append(f"spent ${sc.usd_spent:.4f}, cap ${scenario.max_usd:.4f}")
    if scenario.max_credits is not None and sc.credits_spent > scenario.max_credits:
        failures.append(f"used {sc.credits_spent} search credits, cap {scenario.max_credits}")
    return failures


class EvalRow(BaseModel):
    ts: datetime
    engine_version: str
    scenario: str
    scorecard: Scorecard
    verdict: str
    passed: bool


class EvalStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS eval_runs (ts TEXT, engine_version TEXT, "
                "scenario TEXT, scorecard_json TEXT, verdict TEXT, passed INTEGER)"
            )

    def add(self, row: EvalRow) -> None:
        with closing(sqlite3.connect(self.db_path, timeout=30)) as conn, conn:
            conn.execute(
                "INSERT INTO eval_runs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row.ts.astimezone(UTC).isoformat(),
                    row.engine_version,
                    row.scenario,
                    row.scorecard.model_dump_json(),
                    row.verdict,
                    int(row.passed),
                ),
            )

    def previous(self, scenario: str) -> EvalRow | None:
        """The most recent row for a scenario (call before adding the new one)."""
        with closing(sqlite3.connect(self.db_path, timeout=30)) as conn:
            r = conn.execute(
                "SELECT ts, engine_version, scenario, scorecard_json, verdict, passed "
                "FROM eval_runs WHERE scenario=? ORDER BY ts DESC, rowid DESC LIMIT 1",
                (scenario,),
            ).fetchone()
        if r is None:
            return None
        return EvalRow(
            ts=datetime.fromisoformat(r[0]),
            engine_version=r[1],
            scenario=r[2],
            scorecard=Scorecard.model_validate_json(r[3]),
            verdict=r[4],
            passed=bool(r[5]),
        )
