import pytest
import pytest_asyncio

from kenya_data_engine.home import EngineHome


@pytest.fixture
def tmp_home(tmp_path, monkeypatch) -> EngineHome:
    monkeypatch.delenv("ENGINE_HOME", raising=False)
    for key in ("DEEPSEEK", "TAVILY", "SERPER", "JINA"):
        monkeypatch.delenv(f"{key}_API_KEY", raising=False)
    home = EngineHome(tmp_path)
    home.ensure()
    return home


async def _open(tmp_home):
    from datetime import datetime

    from kenya_data_engine.context import open_context
    from kenya_data_engine.runs import RunStore

    run = RunStore(tmp_home.runs_dir).new_run(datetime(2026, 10, 9, 8, 0))
    async with open_context(tmp_home, run) as c:
        yield c


@pytest_asyncio.fixture
async def ctx(tmp_home, monkeypatch):
    """Context with fake API keys for every provider."""
    for key in ("DEEPSEEK", "TAVILY", "SERPER", "JINA"):
        monkeypatch.setenv(f"{key}_API_KEY", f"fake-{key.lower()}")
    async for c in _open(tmp_home):
        yield c


@pytest_asyncio.fixture
async def ctx_no_keys(tmp_home):
    async for c in _open(tmp_home):
        yield c


def function_model_returning(output):
    """FunctionModel answering every request with the structured-output tool call.

    `output` is a BaseModel/dict, or a callable `(prompt_text) -> BaseModel | dict`
    (it may raise to simulate a failing model call).
    """
    from pydantic import BaseModel
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _prompt(messages) -> str:
        parts = []
        for m in messages:
            if isinstance(m, ModelRequest):
                parts.extend(str(getattr(p, "content", "")) for p in m.parts)
        return "\n".join(parts)

    def fn(messages, info: AgentInfo) -> ModelResponse:
        out = output(_prompt(messages)) if callable(output) else output
        args = out.model_dump() if isinstance(out, BaseModel) else out
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return FunctionModel(fn)
