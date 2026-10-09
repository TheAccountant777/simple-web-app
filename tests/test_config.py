from pathlib import Path

import pytest

from kenya_data_engine.config import load_config, load_secrets
from kenya_data_engine.errors import ConfigError, EngineError
from kenya_data_engine.home import EngineHome


def test_home_resolution_prefers_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_path))
    assert EngineHome.resolve().root == tmp_path


def test_home_resolution_explicit_beats_env_and_default(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_path / "env"))
    assert EngineHome.resolve(tmp_path / "x").root == tmp_path / "x"
    monkeypatch.delenv("ENGINE_HOME")
    assert EngineHome.resolve().root == Path.home() / ".kenya-data-engine"


def test_home_paths_and_ensure(tmp_path):
    home = EngineHome(tmp_path / "h")
    home.ensure()
    assert home.runs_dir.is_dir() and home.briefs_dir.is_dir()
    assert home.config_path == tmp_path / "h" / "config.yaml"
    assert home.calendar_path.name == "calendar.yaml"
    assert home.env_path.name == ".env" and home.db_path.name == "engine.db"


def test_engine_error_carries_hint():
    err = ConfigError("bad", hint="fix it")
    assert isinstance(err, EngineError) and err.message == "bad" and err.hint == "fix it"


def test_defaults_load_without_user_file(tmp_home):
    cfg = load_config(tmp_home)
    assert cfg.top_n == 5 and cfg.budgets.run_usd == 0.50
    assert cfg.weights == {
        "data_ability": 0.35,
        "wallet_impact": 0.20,
        "timeliness": 0.20,
        "clarity_gap": 0.15,
        "novelty": 0.10,
    }
    assert cfg.llm.stages["synthesize_cluster"].model == "deepseek-flash"
    assert cfg.llm.stages["synthesize_score"].model == "deepseek-flash"
    assert cfg.llm.base_url == "https://api.deepseek.com"
    assert cfg.llm.pricing.input_per_m == 0.15 and cfg.llm.pricing.output_per_m == 0.60
    assert cfg.search.providers == ["tavily", "serper"]
    assert cfg.radar.since_hours == 72 and cfg.radar.max_items == 10
    assert set(cfg.radar.listings) == {"cbk", "knbs", "epra", "parliament"}
    assert cfg.radar.trends_feed == "https://trends.google.com/trending/rss?geo=KE"
    assert cfg.cache_ttl_hours == 6.0 and cfg.synth.max_signals == 300


def test_user_yaml_overrides_one_key_keeps_rest(tmp_home):
    tmp_home.config_path.write_text("top_n: 8\n")
    cfg = load_config(tmp_home)
    assert cfg.top_n == 8 and cfg.budgets.run_usd == 0.50


def test_user_yaml_deep_merges_nested(tmp_home):
    tmp_home.config_path.write_text(
        "budgets:\n  run_usd: 1.5\nllm:\n  pricing:\n    input_per_m: 1\n"
    )
    cfg = load_config(tmp_home)
    assert cfg.budgets.run_usd == 1.5
    assert cfg.llm.pricing.input_per_m == 1 and cfg.llm.pricing.output_per_m == 0.60
    assert cfg.llm.base_url == "https://api.deepseek.com"


def test_bad_weights_raise_config_error(tmp_home):
    tmp_home.config_path.write_text(
        "weights: {data_ability: 0.9, wallet_impact: 0.9, timeliness: 0,"
        " clarity_gap: 0, novelty: 0}\n"
    )
    with pytest.raises(ConfigError, match=r"weights must sum to 1\.0"):
        load_config(tmp_home)


def test_invalid_yaml_and_schema_raise_config_error(tmp_home):
    tmp_home.config_path.write_text("top_n: [unclosed\n")
    with pytest.raises(ConfigError):
        load_config(tmp_home)
    tmp_home.config_path.write_text("top_n: many\n")
    with pytest.raises(ConfigError):
        load_config(tmp_home)
    tmp_home.config_path.write_text("- just\n- a list\n")
    with pytest.raises(ConfigError):
        load_config(tmp_home)


def test_env_secret_overrides_dotenv(tmp_home, monkeypatch):
    tmp_home.env_path.write_text("DEEPSEEK_API_KEY=fromfile\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fromenv")
    secrets = load_secrets(tmp_home)
    assert secrets.deepseek_api_key is not None
    assert secrets.deepseek_api_key.get_secret_value() == "fromenv"


def test_dotenv_secret_and_missing_secrets(tmp_home):
    tmp_home.env_path.write_text("TAVILY_API_KEY=tv\n")
    secrets = load_secrets(tmp_home)
    assert secrets.tavily_api_key is not None
    assert secrets.tavily_api_key.get_secret_value() == "tv"
    assert secrets.deepseek_api_key is None and secrets.serper_api_key is None
    assert "tv" not in repr(secrets)
