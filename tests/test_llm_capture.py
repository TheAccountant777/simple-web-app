import json

import pytest
from conftest import function_model_returning
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel

from kenya_data_engine.llm import run_agent


class Out(BaseModel):
    answer: str


async def call(ctx, prompt="hello", name="t", model=None):
    return await run_agent(
        Agent(output_type=Out),
        prompt,
        ctx,
        stage="synthesize_cluster",
        name=name,
        topic_id="tp1",
        model=model or function_model_returning(Out(answer="x")),
    )


def traced_llm(ctx):
    rows = [json.loads(x) for x in ctx.run.trace_path.read_text().splitlines()]
    return [r for r in rows if r["kind"] == "llm"]


async def test_llm_capture_file_written_and_redacted(ctx):
    secret = ctx.secrets.deepseek_api_key.get_secret_value()
    await call(ctx, f"please use {secret} now")
    await call(ctx, "second", name="u")
    files = sorted((ctx.run.dir / "llm").glob("*.json"))
    assert [f.name for f in files] == [
        "0001-synthesize_cluster-t.json",
        "0002-synthesize_cluster-u.json",
    ]
    text = files[0].read_text()
    assert secret not in text and "***" in text
    data = json.loads(text)
    assert data["seq"] == 1 and data["stage"] == "synthesize_cluster" and data["topic_id"] == "tp1"
    assert data["settings"]["max_tokens"] == 4096
    assert data["output"] == {"answer": "x"} and data["status"] == "ok" and data["error"] is None
    assert data["usage"]["input_tokens"] > 0 and data["cost_usd"] > 0 and data["messages"]
    assert traced_llm(ctx)[0]["attrs"]["capture"] == files[0].name


async def test_llm_capture_on_failure(ctx):
    secret = ctx.secrets.deepseek_api_key.get_secret_value()

    def boom(messages, info):
        raise RuntimeError(f"exploded with {secret}")

    with pytest.raises(RuntimeError):
        await call(ctx, "the prompt", model=FunctionModel(boom))
    (f,) = (ctx.run.dir / "llm").glob("*.json")
    data = json.loads(f.read_text())
    assert data["status"] == "error" and secret not in f.read_text()
    assert "***" in data["error"] and "the prompt" in json.dumps(data["messages"])
    assert data["output"] is None


async def test_capture_disabled_writes_nothing(ctx):
    ctx.config.trace.capture_llm = False
    await call(ctx)
    assert not (ctx.run.dir / "llm").exists()
    assert "capture" not in traced_llm(ctx)[0]["attrs"]


class _BrokenDump:
    def dump_python(self, *a, **k):
        raise TypeError("cannot serialise")


async def test_message_dump_failure_never_fails_the_call(ctx, monkeypatch):
    monkeypatch.setattr("kenya_data_engine.llm._MESSAGES", _BrokenDump())
    assert (await call(ctx)).answer == "x"
    (f,) = (ctx.run.dir / "llm").glob("*.json")
    data = json.loads(f.read_text())
    assert data["status"] == "ok" and data["messages"]  # falls back to the prompt alone
    assert traced_llm(ctx)[0]["status"] == "ok"


async def test_messages_not_dumped_when_capture_off(ctx, monkeypatch):
    monkeypatch.setattr("kenya_data_engine.llm._MESSAGES", _BrokenDump())
    ctx.config.trace.capture_llm = False
    assert (await call(ctx)).answer == "x"


async def test_capture_redacts_values_before_json_escaping(ctx):
    tricky = 'sk-"quoted"\\key'  # JSON escaping would hide this from a text-only pass
    ctx.tracer._secrets.append(tricky)
    await call(ctx, f"use {tricky} now", model=function_model_returning(Out(answer=tricky)))
    (f,) = (ctx.run.dir / "llm").glob("*.json")
    data = json.loads(f.read_text())
    assert tricky not in json.dumps(data) and data["output"] == {"answer": "***"}
    assert data["settings"]["max_tokens"] == 4096  # keys such as *_tokens stay intact
    assert data["usage"]["input_tokens"] > 0
