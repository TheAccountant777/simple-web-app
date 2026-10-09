"""JSONL tracing with cost accounting and a run budget."""

import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from kenya_data_engine.errors import BudgetExceeded

log = logging.getLogger(__name__)

_SECRET_KEY = re.compile(r"key|token|secret", re.IGNORECASE)


def _redact(value: Any, secrets: Sequence[str] = ()) -> Any:
    if isinstance(value, dict):
        return {
            k: "***" if _SECRET_KEY.search(str(k)) else _redact(v, secrets)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "***")
    return value


class TraceEvent(BaseModel):
    ts: datetime
    run_id: str
    stage: str
    kind: Literal["llm", "tool", "http", "stage"]
    name: str
    status: Literal["ok", "error"]
    latency_ms: int
    topic_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class Tracer:
    def __init__(
        self,
        path: Path,
        run_id: str,
        input_per_m: float,
        output_per_m: float,
        run_budget_usd: float,
        secrets: Sequence[str] = (),
    ) -> None:
        self.path = path
        self.run_id = run_id
        self.input_per_m = input_per_m
        self.output_per_m = output_per_m
        self.run_budget_usd = run_budget_usd
        # Longest first so a secret that contains another is fully masked.
        self._secrets = sorted((x for x in secrets if x), key=len, reverse=True)
        self.total_cost = 0.0
        self._events: list[TraceEvent] = []
        self._subscribers: list[Callable[[TraceEvent], None]] = []
        self._seed_from_existing()

    def _seed_from_existing(self) -> None:
        """Pick up events of a resumed run so spend and summaries cover the whole run."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                event = TraceEvent.model_validate_json(line)
            except ValueError:
                continue
            self._events.append(event)
            self.total_cost += event.cost_usd

    def redact(self, text: str) -> str:
        """Mask configured secret values in free text."""
        out: str = _redact(text, self._secrets)
        return out

    def subscribe(self, fn: Callable[[TraceEvent], None]) -> Callable[[], None]:
        """Call `fn` with every recorded (redacted) event; returns the unsubscribe function."""
        self._subscribers.append(fn)

        def unsubscribe() -> None:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

        return unsubscribe

    def cost_of(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_m + output_tokens * self.output_per_m) / 1_000_000

    @property
    def remaining_usd(self) -> float:
        return self.run_budget_usd - self.total_cost

    def record(self, event: TraceEvent) -> None:
        self._events.append(event)
        self.total_cost += event.cost_usd
        data = event.model_dump(mode="json")
        data["attrs"] = _redact(data["attrs"], self._secrets)
        if isinstance(data["error"], str):
            data["error"] = _redact(data["error"], self._secrets)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            fh.flush()
        if self._subscribers:
            safe = TraceEvent.model_validate(data)
            for fn in list(self._subscribers):
                try:
                    fn(safe)
                except Exception:
                    self._subscribers.remove(fn)
                    log.warning("trace subscriber %r raised; unsubscribed", fn, exc_info=True)

    def record_llm(
        self,
        stage: str,
        name: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        topic_id: str | None = None,
        status: Literal["ok", "error"] = "ok",
        error: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> float:
        cost = self.cost_of(input_tokens, output_tokens)
        self.record(
            TraceEvent(
                ts=datetime.now(UTC),
                run_id=self.run_id,
                stage=stage,
                kind="llm",
                name=name,
                status=status,
                latency_ms=latency_ms,
                topic_id=topic_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                error=error,
                attrs=attrs or {},
            )
        )
        return cost

    @asynccontextmanager
    async def span(
        self,
        stage: str,
        kind: Literal["llm", "tool", "http", "stage"],
        name: str,
        topic_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        attrs: dict[str, Any] = {}
        start = time.perf_counter()
        error: str | None = None
        try:
            yield attrs
        except BaseException as exc:
            error = str(exc) or type(exc).__name__
            raise
        finally:
            self.record(
                TraceEvent(
                    ts=datetime.now(UTC),
                    run_id=self.run_id,
                    stage=stage,
                    kind=kind,
                    name=name,
                    status="ok" if error is None else "error",
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    topic_id=topic_id,
                    error=error,
                    attrs=attrs,
                )
            )

    def check_budget(self) -> None:
        if self.total_cost >= self.run_budget_usd:
            raise BudgetExceeded(
                f"run budget of ${self.run_budget_usd:.2f} exhausted",
                hint="raise budgets.run_usd in config.yaml",
            )

    def summary(self) -> dict[str, Any]:
        stages: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"events": 0, "cost_usd": 0.0, "errors": 0}
        )
        for e in self._events:
            s = stages[e.stage]
            s["events"] += 1
            s["cost_usd"] += e.cost_usd
            s["errors"] += e.status == "error"
        return {
            "run_id": self.run_id,
            "events": len(self._events),
            "errors": sum(e.status == "error" for e in self._events),
            "total_cost_usd": self.total_cost,
            "stages": dict(stages),
        }
