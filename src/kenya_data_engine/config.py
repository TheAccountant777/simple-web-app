"""Configuration: secrets and the merged engine config."""

from importlib import resources
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from kenya_data_engine.errors import ConfigError
from kenya_data_engine.home import EngineHome


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    deepseek_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    serper_api_key: SecretStr | None = None
    jina_api_key: SecretStr | None = None


def load_secrets(home: EngineHome) -> Secrets:
    """Read secrets from `<home>/.env` and the environment; the environment wins."""
    return Secrets(_env_file=home.env_path)


class Pricing(BaseModel):
    input_per_m: float
    output_per_m: float


class StageLLM(BaseModel):
    model: str
    max_tokens: int
    extra_body: dict[str, Any] = Field(default_factory=dict)


class LLMConfig(BaseModel):
    base_url: str
    pricing: Pricing
    stages: dict[str, StageLLM]


class Budgets(BaseModel):
    run_usd: float = 0.50


class SearchConfig(BaseModel):
    providers: list[str] = Field(default_factory=lambda: ["tavily", "serper"])


class ListingSpec(BaseModel):
    url: str
    item: str
    title: str
    link: str
    date: str | None = None
    kind: str


class RadarConfig(BaseModel):
    since_hours: int = 72
    lookahead_days: int = 21
    max_items: int = 10
    feeds: dict[str, str]
    trends_feed: str
    listings: dict[str, ListingSpec]
    enabled: list[str]


class SynthConfig(BaseModel):
    max_signals: int = 300


class EngineConfig(BaseModel):
    top_n: int = 5
    concurrency: int = 4
    weights: dict[str, float]
    llm: LLMConfig
    budgets: Budgets = Field(default_factory=Budgets)
    search: SearchConfig = Field(default_factory=SearchConfig)
    radar: RadarConfig
    cache_ttl_hours: float = 6.0
    synth: SynthConfig = Field(default_factory=SynthConfig)


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _load_yaml(text: str, where: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {where}", hint=str(exc)) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a mapping", hint="use `key: value` pairs")
    return data


def load_config(home: EngineHome) -> EngineConfig:
    """Deep-merge the user's config.yaml over the packaged defaults and validate."""
    defaults = resources.files("kenya_data_engine").joinpath("defaults/config.yaml")
    merged = _load_yaml(defaults.read_text(encoding="utf-8"), "defaults/config.yaml")
    if home.config_path.exists():
        user = _load_yaml(home.config_path.read_text(encoding="utf-8"), str(home.config_path))
        merged = _deep_merge(merged, user)
    try:
        cfg = EngineConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError("invalid config", hint=str(exc)) from exc
    if abs(sum(cfg.weights.values()) - 1.0) > 1e-6:
        raise ConfigError(
            "weights must sum to 1.0",
            hint=f"edit `weights` in {home.config_path}; current sum is "
            f"{sum(cfg.weights.values()):.3f}",
        )
    return cfg
