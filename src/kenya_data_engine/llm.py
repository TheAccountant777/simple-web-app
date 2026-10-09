"""LLM layer: DeepSeek via Pydantic AI's OpenAI-compatible chat model."""

import time
from importlib import resources
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import ConfigError

_PRIMER_MARKER = "{{primer}}"


def build_model(ctx: RunContext, stage: str = "synthesize_cluster") -> Model:
    key = ctx.secrets.deepseek_api_key
    if key is None:
        raise ConfigError("DEEPSEEK_API_KEY not set", hint="run engine init")
    stage_cfg = ctx.config.llm.stages.get(stage)
    if stage_cfg is None:
        raise ConfigError(f"no llm settings for stage {stage!r}", hint="check `llm.stages`")
    provider = OpenAIProvider(base_url=ctx.config.llm.base_url, api_key=key.get_secret_value())
    return OpenAIChatModel(stage_cfg.model, provider=provider)


def stage_settings(ctx: RunContext, stage: str) -> ModelSettings:
    cfg = ctx.config.llm.stages.get(stage)
    if cfg is None:
        raise ConfigError(f"no llm settings for stage {stage!r}", hint="check `llm.stages`")
    return ModelSettings(max_tokens=cfg.max_tokens, extra_body=dict(cfg.extra_body))


def load_prompt(name: str) -> str:
    """Read `prompts/<name>.md`, expanding a `{{primer}}` marker with `_primer.md`."""
    base = resources.files("kenya_data_engine.prompts")
    try:
        text = base.joinpath(f"{name}.md").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise ConfigError(f"unknown prompt {name!r}", hint="check the prompts directory") from exc
    if _PRIMER_MARKER in text:
        primer = base.joinpath("_primer.md").read_text(encoding="utf-8").strip()
        text = text.replace(_PRIMER_MARKER, primer)
    return text


async def run_agent[T](
    agent: Agent[None, T],
    prompt: str,
    ctx: RunContext,
    *,
    stage: str,
    name: str,
    topic_id: str | None = None,
    model: Model | None = None,
) -> T:
    """Run an agent, tracing tokens and cost. `model` is the test seam."""
    ctx.tracer.check_budget()
    use_model = model if model is not None else build_model(ctx, stage)
    settings = stage_settings(ctx, stage)
    usage = RunUsage()
    start = time.perf_counter()

    def record(status: str, error: str | None = None) -> None:
        ctx.tracer.record_llm(
            stage,
            name,
            usage.input_tokens,
            usage.output_tokens,
            int((time.perf_counter() - start) * 1000),
            topic_id=topic_id,
            status="ok" if status == "ok" else "error",
            error=error,
        )

    try:
        result: Any = await agent.run(prompt, model=use_model, model_settings=settings, usage=usage)
    except BaseException as exc:
        record("error", str(exc) or type(exc).__name__)
        raise
    record("ok")
    output: T = result.output
    return output
