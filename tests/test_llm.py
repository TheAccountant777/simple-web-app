import json

import pytest
from conftest import function_model_returning
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.models.function import FunctionModel

from kenya_data_engine.errors import BudgetExceeded, ConfigError, EngineError
from kenya_data_engine.llm import build_model, load_prompt, run_agent, stage_settings


class Out(BaseModel):
    answer: str


async def test_run_agent_records_usage_and_cost(ctx):
    out = await run_agent(
        Agent(output_type=Out),
        "hello there",
        ctx,
        stage="synthesize_cluster",
        name="t",
        model=function_model_returning(Out(answer="x")),
    )
    assert out == Out(answer="x")
    lines = [json.loads(x) for x in ctx.run.trace_path.read_text().splitlines()]
    llm = [x for x in lines if x["kind"] == "llm"]
    assert len(llm) == 1 and llm[0]["cost_usd"] > 0 and llm[0]["stage"] == "synthesize_cluster"
    assert ctx.tracer.total_cost > 0


async def test_run_agent_checks_budget_first(ctx):
    ctx.tracer.total_cost = ctx.tracer.run_budget_usd
    called = []

    def fn(messages, info):
        called.append(1)
        raise AssertionError

    with pytest.raises(BudgetExceeded):
        await run_agent(
            Agent(output_type=Out), "p", ctx, stage="s", name="n", model=FunctionModel(fn)
        )
    assert not called


async def test_run_agent_records_failure(ctx):
    def boom(_):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await run_agent(
            Agent(output_type=Out),
            "p",
            ctx,
            stage="synthesize_score",
            name="n",
            topic_id="t1",
            model=function_model_returning(boom),
        )
    lines = [json.loads(x) for x in ctx.run.trace_path.read_text().splitlines()]
    assert [x["status"] for x in lines if x["kind"] == "llm"] == ["error"]


async def test_run_agent_builds_model_when_none(ctx_no_keys):
    with pytest.raises(ConfigError):
        await run_agent(Agent(output_type=Out), "p", ctx_no_keys, stage="s", name="n")


def test_build_model_without_key_raises(ctx_no_keys):
    with pytest.raises(ConfigError) as e:
        build_model(ctx_no_keys)
    assert e.value.hint == "run engine init"


def test_build_model_with_key(ctx):
    m = build_model(ctx)
    assert m.model_name == "deepseek-flash"
    assert "deepseek" in str(m.base_url)


def test_stage_settings(ctx):
    ctx.config.llm.stages["synthesize_cluster"].extra_body = {"thinking": {"type": "disabled"}}
    s = stage_settings(ctx, "synthesize_cluster")
    assert s["max_tokens"] == 4096 and s["extra_body"] == {"thinking": {"type": "disabled"}}


def test_stage_settings_unknown_stage(ctx):
    with pytest.raises(ConfigError):
        stage_settings(ctx, "nope")


def test_load_prompt_includes_primer():
    text = load_prompt("cluster")
    assert "{{primer}}" not in text
    assert "Kenya" in text


def test_load_prompt_unknown():
    with pytest.raises(ConfigError):
        load_prompt("nope")


def _raising(exc):
    def fn(messages, info):
        raise exc

    return FunctionModel(fn)


@pytest.mark.parametrize(
    ("exc", "klass", "message", "hint"),
    [
        (
            ModelHTTPError(401, "m"),
            ConfigError,
            "DeepSeek rejected the API key",
            "run `engine init`",
        ),
        (
            ModelHTTPError(403, "m"),
            ConfigError,
            "DeepSeek rejected the API key",
            "run `engine init`",
        ),
        (
            ModelHTTPError(402, "m"),
            EngineError,
            "DeepSeek balance exhausted",
            "top up your DeepSeek account",
        ),
        (
            ModelHTTPError(429, "m"),
            EngineError,
            "DeepSeek is rate-limiting or unavailable",
            "retry in a few minutes",
        ),
        (
            ModelHTTPError(400, "m"),
            EngineError,
            "DeepSeek rejected the request",
            "check llm.stages in config.yaml (e.g. extra_body); re-run with -v for details",
        ),
        (
            ModelHTTPError(503, "m"),
            EngineError,
            "DeepSeek is rate-limiting or unavailable",
            "retry in a few minutes",
        ),
        (
            ModelAPIError("m", "connection refused"),
            EngineError,
            "could not reach DeepSeek",
            "check your network; run `engine doctor`",
        ),
        (
            UnexpectedModelBehavior("bad output"),
            EngineError,
            "DeepSeek returned output that failed validation",
            "re-run; if it persists, report with `-v`",
        ),
    ],
)
async def test_run_agent_maps_provider_errors(ctx, exc, klass, message, hint):
    with pytest.raises(klass) as e:
        await run_agent(
            Agent(output_type=Out), "p", ctx, stage="synthesize_cluster", name="n",
            model=_raising(exc),
        )  # fmt: skip
    assert type(e.value) is klass
    assert e.value.message == message and e.value.hint == hint
    assert e.value.__cause__ is exc
    lines = [json.loads(x) for x in ctx.run.trace_path.read_text().splitlines()]
    assert [x["status"] for x in lines if x["kind"] == "llm"] == ["error"]


async def test_run_agent_unmapped_status_passes_through(ctx):
    with pytest.raises(ModelHTTPError):
        await run_agent(
            Agent(output_type=Out), "p", ctx, stage="synthesize_cluster", name="n",
            model=_raising(ModelHTTPError(404, "m")),
        )  # fmt: skip


class Box(BaseModel):
    value: str


async def test_run_agent_with_deps_and_tools(ctx):
    from pydantic_ai import RunContext as AiCtx
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

    agent = Agent(deps_type=Box, output_type=Out)

    @agent.tool
    def read_box(c: AiCtx[Box]) -> str:
        return c.deps.value

    def fn(messages, info):
        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart):
                    return ModelResponse(
                        parts=[ToolCallPart(info.output_tools[0].name, {"answer": p.content})]
                    )
        return ModelResponse(parts=[ToolCallPart("read_box", {})])

    assert TextPart  # keep import used
    out = await run_agent(
        agent, "go", ctx, stage="synthesize_cluster", name="t",
        model=FunctionModel(fn), deps=Box(value="from-deps"),
    )  # fmt: skip
    assert out.answer == "from-deps"


async def test_run_agent_usage_limit_maps_to_engine_error(ctx):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.usage import UsageLimits

    agent = Agent(output_type=Out)

    @agent.tool_plain
    def ping() -> str:
        return "pong"

    def fn(messages, info):
        return ModelResponse(parts=[ToolCallPart("ping", {})])

    with pytest.raises(EngineError) as ei:
        await run_agent(
            agent, "go", ctx, stage="synthesize_cluster", name="loop",
            model=FunctionModel(fn), usage_limits=UsageLimits(request_limit=1),
        )  # fmt: skip
    assert "limit" in ei.value.message and "loop" in ei.value.message


def _ledger(ctx):
    from kenya_data_engine.research.budget import Ledger

    return Ledger.from_config(ctx.config.research)


async def test_ledger_model_reserves_and_settles(ctx):
    ledger = _ledger(ctx)
    await run_agent(
        Agent(output_type=Out), "hello", ctx, stage="synthesize_cluster", name="t",
        model=function_model_returning(Out(answer="x")), ledger=ledger, group="planner",
    )  # fmt: skip
    g = ledger.snapshot().groups["planner"]
    assert g.usd_spent > 0 and g.usd_reserved == 0


async def test_ledger_model_refuses_when_exhausted(ctx):
    ledger = _ledger(ctx)
    ledger.settle(ledger.reserve("planner", 0.0225), 0.0225)
    calls = []

    def fn(messages, info):
        calls.append(1)
        raise AssertionError

    with pytest.raises(BudgetExceeded):
        await run_agent(
            Agent(output_type=Out), "hello", ctx, stage="synthesize_cluster", name="t",
            model=FunctionModel(fn), ledger=ledger, group="planner",
        )  # fmt: skip
    assert calls == []


async def test_ledger_model_settles_zero_on_failure(ctx):
    ledger = _ledger(ctx)

    def boom(_):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await run_agent(
            Agent(output_type=Out), "hello", ctx, stage="synthesize_cluster", name="t",
            model=function_model_returning(boom), ledger=ledger, group="planner",
        )  # fmt: skip
    g = ledger.snapshot().groups["planner"]
    assert g.usd_spent == 0 and g.usd_reserved == 0
