"""LLM layer: DeepSeek via Pydantic AI's OpenAI-compatible chat model."""

import contextlib
import json
import logging
import math
import time
from importlib import resources
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import ConfigError, EngineError
from kenya_data_engine.research.budget import Ledger
from kenya_data_engine.trace import Tracer

log = logging.getLogger(__name__)

_PRIMER_MARKER = "{{primer}}"
DEFAULT_MAX_TOKENS = 4096  # output reservation when a stage sets no max_tokens


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


def _friendly(exc: BaseException) -> EngineError | None:
    """Map a provider failure to a user-facing error, or None to let it propagate."""
    if isinstance(exc, ModelHTTPError):
        code = exc.status_code
        if code in (401, 403):
            return ConfigError("DeepSeek rejected the API key", hint="run `engine init`")
        if code == 402:
            return EngineError("DeepSeek balance exhausted", hint="top up your DeepSeek account")
        if code == 429 or code >= 500:
            return EngineError(
                "DeepSeek is rate-limiting or unavailable", hint="retry in a few minutes"
            )
        if code == 400:
            return EngineError(
                "DeepSeek rejected the request",
                hint=(
                    "check llm.stages in config.yaml (e.g. extra_body); re-run with -v for details"
                ),
            )
        return None
    if isinstance(exc, ModelAPIError):
        return EngineError(
            "could not reach DeepSeek", hint="check your network; run `engine doctor`"
        )
    if isinstance(exc, UnexpectedModelBehavior):
        return EngineError(
            "DeepSeek returned output that failed validation",
            hint="re-run; if it persists, report with `-v`",
        )
    return None


_MESSAGES = TypeAdapter(list[ModelMessage])


def _write_capture(ctx: RunContext, payload: dict[str, Any], stage: str, name: str) -> str:
    """Write one redacted exchange file under `<run>/llm/`; returns its file name."""
    directory = ctx.run.dir / "llm"
    directory.mkdir(exist_ok=True)
    seq = max((int(p.name[:4]) for p in directory.glob("[0-9][0-9][0-9][0-9]-*.json")), default=0)
    seq += 1
    payload["seq"] = seq
    filename = f"{seq:04d}-{stage}-{name}.json"
    # Redact values first: JSON escaping can hide a secret from the text pass.
    safe = ctx.tracer.redact_data(payload)
    text = ctx.tracer.redact(json.dumps(safe, ensure_ascii=False, indent=2, default=str))
    (directory / filename).write_text(text, encoding="utf-8")
    return filename


def _chars(messages: list[ModelMessage]) -> int:
    """Rough size of a request in characters, for the pre-call cost estimate."""
    total = 0
    for m in messages:
        for part in m.parts:
            content = getattr(part, "content", None)
            if content is None:
                content = getattr(part, "args", "")
            total += len(content if isinstance(content, str) else str(content))
            total += len(getattr(part, "tool_name", ""))
        if isinstance(m, ModelRequest) and m.instructions:
            total += len(m.instructions)
    return total


class LedgerModel(WrapperModel):
    """Wrap a model so every request reserves budget first and settles on the real usage.

    Caps are soft by the input-estimate error: the estimate (characters / 3) can differ from the
    real token count, so spend may pass a cap by that error. The next reserve is still refused.
    Streaming is not supported, since it would bypass the ledger.
    """

    def __init__(
        self, wrapped: Model, ledger: Ledger, group: str, tracer: Tracer, max_tokens: int
    ) -> None:
        super().__init__(wrapped)
        self.ledger = ledger
        self.group = group
        self.tracer = tracer
        self.max_tokens = max_tokens

    def request_stream(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("streaming is not supported under a budget ledger")

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.tracer.check_budget()
        estimate = self.tracer.cost_of(math.ceil(_chars(messages) / 3), self.max_tokens)
        reservation = self.ledger.reserve(self.group, estimate)
        try:
            response = await self.wrapped.request(
                messages, model_settings, model_request_parameters
            )
        except BaseException:
            self.ledger.settle(reservation, 0.0)
            raise
        u = response.usage
        self.ledger.settle(
            reservation,
            self.tracer.cost_of(u.input_tokens, u.output_tokens, u.cache_read_tokens),
        )
        return response


async def run_agent[D, T](
    agent: Agent[D, T],
    prompt: str,
    ctx: RunContext,
    *,
    stage: str,
    name: str,
    topic_id: str | None = None,
    model: Model | None = None,
    deps: D | None = None,
    usage_limits: UsageLimits | None = None,
    ledger: Ledger | None = None,
    group: str | None = None,
) -> T:
    """Run an agent, tracing tokens and cost. `model` is the test seam.

    With a `ledger`, every model request reserves and settles budget in `group` (default: the
    stage name). `usage_limits` caps requests and tool calls inside the run.
    """
    ctx.tracer.check_budget()
    use_model = model if model is not None else build_model(ctx, stage)
    settings = stage_settings(ctx, stage)
    if ledger is not None:
        use_model = LedgerModel(
            use_model,
            ledger,
            group or stage,
            ctx.tracer,
            settings.get("max_tokens") or DEFAULT_MAX_TOKENS,
        )
    usage = RunUsage()
    start = time.perf_counter()

    def record(
        status: str,
        error: str | None = None,
        result: Any = None,
        output: Any = None,
    ) -> None:
        latency = int((time.perf_counter() - start) * 1000)
        attrs: dict[str, Any] = {}
        if ctx.config.trace.capture_llm:
            messages: list[Any] = [
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": prompt}]}
            ]
            if result is not None:
                try:
                    messages = _MESSAGES.dump_python(result.all_messages(), mode="json")
                except Exception:  # a dump problem must never fail a successful call
                    log.warning("could not serialise LLM messages for capture", exc_info=True)
            payload = {
                "stage": stage,
                "name": name,
                "topic_id": topic_id,
                "model": getattr(use_model, "model_name", str(use_model)),
                "settings": {
                    "max_tokens": settings.get("max_tokens"),
                    "extra_body": settings.get("extra_body"),
                },
                "messages": messages,
                "output": output.model_dump(mode="json")
                if isinstance(output, BaseModel)
                else output,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                },
                "latency_ms": latency,
                "cost_usd": ctx.tracer.cost_of(
                    usage.input_tokens, usage.output_tokens, usage.cache_read_tokens
                ),
                "status": status,
                "error": ctx.tracer.redact(error) if error else None,
            }
            with contextlib.suppress(OSError):  # diagnostics must never break a run
                attrs["capture"] = _write_capture(ctx, payload, stage, name)
        ctx.tracer.record_llm(
            stage,
            name,
            usage.input_tokens,
            usage.output_tokens,
            latency,
            topic_id=topic_id,
            status="ok" if status == "ok" else "error",
            error=error,
            attrs=attrs,
            cache_read_tokens=usage.cache_read_tokens,
        )

    try:
        run_result: Any = await agent.run(
            prompt,
            model=use_model,
            model_settings=settings,
            usage=usage,
            deps=cast("D", deps),  # None only for agents without deps
            usage_limits=usage_limits,
        )
    except BaseException as exc:
        record("error", str(exc) or type(exc).__name__)
        if isinstance(exc, UsageLimitExceeded):
            raise EngineError(
                f"agent {name!r} hit its usage limit: {exc}",
                hint="the agent used more model requests or tool calls than allowed",
            ) from exc
        friendly = _friendly(exc)
        if friendly is not None:
            raise friendly from exc
        raise
    output: T = run_result.output
    record("ok", result=run_result, output=output)
    return output
