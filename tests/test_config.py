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
    for stage in ("synthesize_cluster", "synthesize_score"):
        assert cfg.llm.stages[stage].extra_body == {"thinking": {"type": "disabled"}}
    assert cfg.llm.base_url == "https://api.deepseek.com"
    assert cfg.llm.pricing.input_per_m == 0.15 and cfg.llm.pricing.output_per_m == 0.60
    assert cfg.search.providers == ["tavily", "serper"]
    assert cfg.radar.since_hours == 72 and cfg.radar.max_items == 10
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


def test_weights_must_have_exact_keys(tmp_home):
    tmp_home.config_path.write_text("weights: {data_ability: 0.5, wallet_impact: 0.5}\n")
    with pytest.raises(ConfigError) as ei:
        load_config(tmp_home)
    assert ei.value.hint


def test_weights_must_be_non_negative(tmp_home):
    tmp_home.config_path.write_text(
        "weights: {data_ability: 0.85, wallet_impact: 0.2, timeliness: 0.2,"
        " clarity_gap: 0.15, novelty: -0.4}\n"
    )
    with pytest.raises(ConfigError) as ei:
        load_config(tmp_home)
    assert ei.value.hint


@pytest.mark.parametrize("bad", [".nan", ".inf"])
def test_weights_reject_non_finite(tmp_home, bad):
    tmp_home.config_path.write_text(
        f"weights: {{data_ability: {bad}, wallet_impact: 0.2, timeliness: 0.2,"
        " clarity_gap: 0.15, novelty: 0.1}\n"
    )
    with pytest.raises(ConfigError):
        load_config(tmp_home)


def test_unknown_weight_key_rejected(tmp_home):
    tmp_home.config_path.write_text("weights: {noveltty: 0.1}\n")
    with pytest.raises(ConfigError) as ei:
        load_config(tmp_home)
    assert "noveltty" in (ei.value.hint or "")


def test_blank_secret_counts_as_missing(tmp_home):
    tmp_home.env_path.write_text("DEEPSEEK_API_KEY=\nTAVILY_API_KEY=   \nSERPER_API_KEY=abc\n")
    s = load_secrets(tmp_home)
    assert s.deepseek_api_key is None
    assert s.tavily_api_key is None
    assert s.serper_api_key is not None


def test_unknown_top_level_key_rejected(tmp_home):
    tmp_home.config_path.write_text("topn: 8\n")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_home)
    assert "topn" in e.value.message


def test_unknown_nested_key_rejected(tmp_home):
    tmp_home.config_path.write_text("budgets:\n  run_dollars: 1\nradar:\n  since_hour: 5\n")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_home)
    assert "run_dollars" in (e.value.hint or "") or "run_dollars" in e.value.message
