import json

import httpx
import pytest
from pydantic import BaseModel

from kenya_data_engine.context import StageEvent, open_context
from kenya_data_engine.pipeline import run_pipeline


class AOut(BaseModel):
    n: int


class BOut(BaseModel):
    total: int


class A:
    name = "a"
    output_name = "a"
    output_type = AOut
    calls = 0

    async def run(self, ctx, inp):
        type(self).calls += 1
        assert inp is None
        return AOut(n=2)


class B:
    name = "b"
    output_name = "b"
    output_type = BOut
    calls = 0
    seen: AOut | None = None

    async def run(self, ctx, inp):
        type(self).calls += 1
        type(self).seen = inp
        return BOut(total=inp.n * 10)


class Boom(B):
    async def run(self, ctx, inp):
        raise RuntimeError("kaboom")


@pytest.fixture(autouse=True)
def reset_counts():
    A.calls = B.calls = 0
    B.seen = None


@pytest.fixture
def events(ctx):
    got: list[StageEvent] = []
    ctx.emit = got.append
    return got


async def test_chains_outputs_and_writes_artifacts(ctx, events):
    out = await run_pipeline([A(), B()], ctx)
    assert out == BOut(total=20) and B.seen == AOut(n=2)
    assert (ctx.run.dir / "a.json").exists() and (ctx.run.dir / "b.json").exists()
    assert [(e.stage, e.status) for e in events] == [
        ("a", "start"),
        ("a", "done"),
        ("b", "start"),
        ("b", "done"),
    ]
    summary = json.loads((ctx.run.dir / "summary.json").read_text())
    assert summary["errors"] == 0 and set(summary["stages"]) == {"a", "b"}
    assert ctx.tracer.summary()["stages"]["a"]["events"] == 1


async def test_resume_skips_completed_stage(ctx, events):
    ctx.run.write("a", AOut(n=5))
    out = await run_pipeline([A(), B()], ctx, resume=True)
    assert A.calls == 0 and B.calls == 1
    assert B.seen == AOut(n=5) and out == BOut(total=50)
    assert ("a", "skip") in [(e.stage, e.status) for e in events]


async def test_no_resume_reruns_everything(ctx):
    ctx.run.write("a", AOut(n=5))
    await run_pipeline([A(), B()], ctx)
    assert A.calls == 1


async def test_resume_reruns_stage_with_corrupt_artifact(ctx):
    (ctx.run.dir / "a.json").write_text("{bad")
    await run_pipeline([A(), B()], ctx, resume=True)
    assert A.calls == 1
    assert json.loads((ctx.run.dir / "a.json").read_text()) == {"n": 2}


async def test_failure_emits_error_and_writes_summary(ctx, events):
    with pytest.raises(RuntimeError, match="kaboom"):
        await run_pipeline([A(), Boom()], ctx)
    assert (events[-1].stage, events[-1].status) == ("b", "error")
    assert "kaboom" in events[-1].detail
    summary = json.loads((ctx.run.dir / "summary.json").read_text())
    assert summary["errors"] == 1


async def test_empty_pipeline_rejected(ctx):
    with pytest.raises(ValueError):
        await run_pipeline([], ctx)


async def test_open_context_wires_config_and_closes_client(tmp_home):
    from datetime import datetime

    from kenya_data_engine.runs import RunStore

    run = RunStore(tmp_home.runs_dir).new_run(datetime(2026, 10, 9, 9, 0))
    async with open_context(tmp_home, run) as c:
        assert c.config.top_n == 5 and c.tracer.run_id == run.run_id
        assert c.tracer.run_budget_usd == c.config.budgets.run_usd
        c.emit(StageEvent(stage="x", status="start"))  # default emit is a no-op
        http: httpx.AsyncClient = c.http
        assert not http.is_closed
    assert http.is_closed


async def test_resume_reruns_all_stages_after_first_rerun(ctx, events):
    (ctx.run.dir / "a.json").write_text("{bad")
    ctx.run.write("b", BOut(total=999))  # stale downstream artifact
    out = await run_pipeline([A(), B()], ctx, resume=True)
    assert A.calls == 1 and B.calls == 1
    assert out == BOut(total=20)
    assert ("b", "skip") not in [(e.stage, e.status) for e in events]


async def test_stage_error_detail_is_redacted(ctx, events):
    class Leaky(B):
        async def run(self, ctx, inp):
            raise RuntimeError("failed with key fake-deepseek here")

    with pytest.raises(RuntimeError):
        await run_pipeline([A(), Leaky()], ctx)
    assert "fake-deepseek" not in events[-1].detail and "***" in events[-1].detail
