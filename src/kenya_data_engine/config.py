"""Configuration: secrets and the merged engine config."""

import math
from importlib import resources
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from kenya_data_engine.errors import ConfigError
from kenya_data_engine.home import EngineHome

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
    cache_hit_input_per_m: float | None = None


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


class RadarConfig(_Strict):
    """Radar tuning. The sources themselves live in sources.yaml."""

    since_hours: int = 72
    lookahead_days: int = 21
    max_items: int = 10
    source_timeout_s: float = Field(default=20.0, gt=0)


class SynthConfig(_Strict):
    max_signals: int = 300


class BudgetPreset(_Strict):
    usd: float
    search_credits: int
    seconds: float


class ResearchConfig(_Strict):
    default_budget: Literal["lean", "standard", "deep"] = "standard"
    presets: dict[str, BudgetPreset]
    shares: dict[str, float]
    search_shares: dict[str, float]
    protected: list[str]
    scout_concurrency: int = 3
    monthly_search_credits: int = 1000

    @model_validator(mode="after")
    def _shares_sum_to_one(self) -> "ResearchConfig":
        for label, shares in (("shares", self.shares), ("search_shares", self.search_shares)):
            if abs(sum(shares.values()) - 1.0) > 1e-6:
                raise ValueError(f"research.{label} must sum to 1.0, got {sum(shares.values())}")
        return self


class DataConfig(_Strict):
    max_bytes: dict[str, int]
    max_redirects: int = 5
    pdf_max_pages: int = 60
    parse_timeout_s: float = 30
    per_domain_concurrency: int = 2


class TraceConfig(_Strict):
    capture_llm: bool = True


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
    trace: TraceConfig = Field(default_factory=TraceConfig)
    research: ResearchConfig
    data: DataConfig


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


LEGACY_RADAR_KEYS = frozenset({"feeds", "trends_feed", "listings", "enabled", "user_agents"})


def _user_config(home: EngineHome) -> dict[str, Any]:
    if not home.config_path.exists():
        return {}
    return _load_yaml(home.config_path.read_text(encoding="utf-8"), str(home.config_path))


def legacy_radar_keys(home: EngineHome) -> list[str]:
    """Pre-sources.yaml `radar.*` keys still present in the user's config.yaml (sorted)."""
    try:
        radar = _user_config(home).get("radar")
    except ConfigError:
        return []
    return sorted(LEGACY_RADAR_KEYS & set(radar)) if isinstance(radar, dict) else []


def load_config(home: EngineHome) -> EngineConfig:
    """Deep-merge the user's config.yaml over the packaged defaults and validate."""
    defaults = resources.files("kenya_data_engine").joinpath("defaults/config.yaml")
    merged = _load_yaml(defaults.read_text(encoding="utf-8"), "defaults/config.yaml")
    user = _user_config(home)
    radar = user.get("radar")
    if isinstance(radar, dict):  # legacy keys are ignored; `engine doctor` warns about them
        user["radar"] = {k: v for k, v in radar.items() if k not in LEGACY_RADAR_KEYS}
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


class SourceSpec(_Strict):
    """One Radar source, as written in sources.yaml."""

    type: Literal["rss", "listing"]
    url: str
    kind: Literal["news", "data_release", "policy", "attention"]
    enabled: bool = True
    user_agent: str | None = None
    item: str | None = None  # listing only: CSS selectors
    title: str | None = None
    link: str | None = None
    date: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _listing_needs_selectors(self) -> "SourceSpec":
        if self.type == "listing":
            missing = [f for f in ("item", "title", "link") if not getattr(self, f)]
            if missing:
                raise ValueError(f"listing sources need {', '.join(missing)}")
        return self


class Sources(BaseModel):
    specs: dict[str, SourceSpec] = Field(default_factory=dict)
    invalid: dict[str, str] = Field(default_factory=dict)  # name -> what is wrong


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(x) for x in e['loc']) or 'entry'}: {e['msg']}" for e in exc.errors()
    )


def load_sources(home: EngineHome) -> Sources:
    """Merge `<home>/sources.yaml` over the packaged sources by name, field by field.

    Never raises: a broken entry (or an unreadable user file) lands in `invalid` and the
    rest still load.
    """
    packaged = resources.files("kenya_data_engine").joinpath("defaults/sources.yaml")
    raw: dict[str, Any] = _load_yaml(packaged.read_text(encoding="utf-8"), "defaults/sources.yaml")
    raw = {str(k): v for k, v in raw.items()}
    out = Sources()
    if home.sources_path.exists():
        try:
            user = _load_yaml(home.sources_path.read_text(encoding="utf-8"), "sources.yaml")
        except ConfigError as exc:
            out.invalid["sources.yaml"] = exc.message
            user = {}
        for name, over in user.items():
            base = raw.get(str(name))
            raw[str(name)] = (
                {**base, **over} if isinstance(base, dict) and isinstance(over, dict) else over
            )
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            out.invalid[name] = "entry must be a mapping of fields"
            continue
        try:
            out.specs[name] = SourceSpec.model_validate(entry)
        except ValidationError as exc:
            out.invalid[name] = _describe(exc)
    return out
