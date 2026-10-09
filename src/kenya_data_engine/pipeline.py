"""Resumable stage orchestrator."""

import json
from collections.abc import Sequence
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from kenya_data_engine.context import RunContext, StageEvent

I = TypeVar("I", contravariant=True)  # noqa: E741
O = TypeVar("O", bound=BaseModel)  # noqa: E741


class Stage(Protocol[I, O]):
    name: str
    output_name: str
    output_type: type[O]

    async def run(self, ctx: RunContext, inp: I) -> O: ...


def _write_summary(ctx: RunContext) -> None:
    path = ctx.run.dir / "summary.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ctx.tracer.summary(), indent=2), encoding="utf-8")
    tmp.replace(path)


async def run_pipeline(
    stages: Sequence[Stage[Any, Any]], ctx: RunContext, *, resume: bool = False
) -> BaseModel:
    if not stages:
        raise ValueError("run_pipeline needs at least one stage")
    current: Any = None
    rerun = False  # once a stage really runs, every later stage must too
    try:
        for stage in stages:
            if resume and not rerun:
                existing = ctx.run.read(stage.output_name, stage.output_type)
                if existing is not None:
                    ctx.emit(StageEvent(stage=stage.name, status="skip", detail="artifact found"))
                    current = existing
                    continue
            rerun = True
            ctx.emit(StageEvent(stage=stage.name, status="start"))
            try:
                async with ctx.tracer.span(stage.name, "stage", stage.name):
                    current = await stage.run(ctx, current)
                ctx.run.write(stage.output_name, current)
            except Exception as exc:
                ctx.emit(
                    StageEvent(stage=stage.name, status="error", detail=ctx.tracer.redact(str(exc)))
                )
                raise
            ctx.emit(StageEvent(stage=stage.name, status="done"))
    finally:
        _write_summary(ctx)
    assert isinstance(current, BaseModel)
    return current
