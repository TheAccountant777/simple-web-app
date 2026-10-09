"""Configuration: secrets and the merged engine config."""

import math
from importlib import resources
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kenya_data_engine.errors import ConfigError
from kenya_data_engine.home import EngineHome
from kenya_data_engine.models import SignalKind

WEIGHT_KEYS = frozenset({"data_ability", "wallet_impact", "timeliness", "clarity_gap", "novelty"})


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    deepseek_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    serper_api_key: SecretStr | None = None
    jina_api_key: SecretStr | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


def load_secrets(home: EngineHome) -> Secrets:
    """Read secrets from `<home>/.env` and the environment; the environment wins."""
    return Secrets(_env_file=home.env_path)


class _Strict(BaseModel):
    """Base for config models: unknown keys are errors, not silently ignored."""

    model_config = ConfigDict(extra="forbid")


class Pricing(_Strict):
    input_per_m: float
    output_per_m: float


class StageLLM(_Strict):
    model: str
    max_tokens: int
    extra_body: dict[str, Any] = Field(default_factory=dict)


class LLMConfig(_Strict):
    base_url: str
    pricing: Pricing
    stages: dict[str, StageLLM]


class Budgets(_Strict):
    run_usd: float = 0.50


class SearchConfig(_Strict):
    providers: list[str] = Field(default_factory=lambda: ["tavily", "serper"])


class ListingSpec(_Strict):
    url: str
    item: str
    title: str
    link: str
    date: str | None = None
    kind: SignalKind


class RadarConfig(_Strict):
    since_hours: int = 72
    lookahead_days: int = 21
    max_items: int = 10
    feeds: dict[str, str]
    trends_feed: str
    listings: dict[str, ListingSpec]
    enabled: list[str]


class SynthConfig(_Strict):
    max_signals: int = 300


class EngineConfig(_Strict):
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
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first["loc"])
        what = "unknown key" if first["type"] == "extra_forbidden" else first["msg"]
        raise ConfigError(f"invalid config: {what} `{where}`", hint=str(exc)) from exc
    bad_values = any(not math.isfinite(v) or v < 0 for v in cfg.weights.values())
    if set(cfg.weights) != WEIGHT_KEYS or bad_values:
        raise ConfigError(
            "weights must have exactly the keys "
            + ", ".join(sorted(WEIGHT_KEYS))
            + " and be finite and >= 0",
            hint=f"edit `weights` in {home.config_path}; current keys are "
            f"{', '.join(sorted(cfg.weights))}",
        )
    if abs(sum(cfg.weights.values()) - 1.0) > 1e-6:
        raise ConfigError(
            "weights must sum to 1.0",
            hint=f"edit `weights` in {home.config_path}; current sum is "
            f"{sum(cfg.weights.values()):.3f}",
        )
    return cfg
