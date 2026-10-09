import pytest

from kenya_data_engine.home import EngineHome


@pytest.fixture
def tmp_home(tmp_path, monkeypatch) -> EngineHome:
    monkeypatch.delenv("ENGINE_HOME", raising=False)
    for key in ("DEEPSEEK", "TAVILY", "SERPER", "JINA"):
        monkeypatch.delenv(f"{key}_API_KEY", raising=False)
    home = EngineHome(tmp_path)
    home.ensure()
    return home
