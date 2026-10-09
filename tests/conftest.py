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


@pytest_asyncio.fixture
async def ctx(tmp_home):
    from datetime import datetime

    from kenya_data_engine.context import open_context
    from kenya_data_engine.runs import RunStore

    run = RunStore(tmp_home.runs_dir).new_run(datetime(2026, 10, 9, 8, 0))
    async with open_context(tmp_home, run) as c:
        yield c
