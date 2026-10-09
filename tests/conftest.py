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
async def ctx(tmp_home, monkeypatch, data_net):
    """Context with fake API keys for every provider."""
    for key in ("DEEPSEEK", "TAVILY", "SERPER", "JINA"):
        monkeypatch.setenv(f"{key}_API_KEY", f"fake-{key.lower()}")
    async for c in _open(tmp_home):
        yield c


@pytest_asyncio.fixture
async def ctx_no_keys(tmp_home, data_net):
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


def mock_sources(respx_mock, home) -> None:
    """Serve a minimal valid response for every source configured under `home`.

    Feeds get generic RSS and listings get HTML generated from their own selectors, so adding
    a source never needs a new fixture file.
    """
    from sourcegen import listing_html, rss_xml

    from kenya_data_engine.config import load_sources

    for name, spec in load_sources(home).specs.items():
        body = rss_xml(name) if spec.type == "rss" else listing_html(name, spec)
        respx_mock.get(spec.url).respond(200, content=body.encode())


@pytest.fixture
def data_net(monkeypatch):
    """Data-layer HTTP without DNS or retry sleeps: every host resolves to a public address."""
    import tenacity

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    import kenya_data_engine.context as context

    real = context.RunContext

    def with_resolver(**kw):
        return real(**{"resolver": resolve, **kw})

    # Every context opened from here on (CLI included) carries the fake resolver.
    monkeypatch.setattr(context, "RunContext", with_resolver)
    monkeypatch.setattr("kenya_data_engine.http._WAIT", tenacity.wait_none())
