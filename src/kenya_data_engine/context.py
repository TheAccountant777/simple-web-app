"""Shared per-run context handed to every stage and tool."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from kenya_data_engine.cache import Cache
from kenya_data_engine.config import EngineConfig, Secrets, load_config, load_secrets
from kenya_data_engine.home import EngineHome
from kenya_data_engine.runs import RunHandle
from kenya_data_engine.tls import AiaFixer
from kenya_data_engine.tools.urlpolicy import Resolver
from kenya_data_engine.trace import Tracer


class StageEvent(BaseModel):
    stage: str
    status: Literal["start", "skip", "done", "error"]
    detail: str = ""


def _no_emit(event: StageEvent) -> None:
    return None


@dataclass
class RunContext:
    home: EngineHome
    config: EngineConfig
    secrets: Secrets
    run: RunHandle
    tracer: Tracer
    cache: Cache
    http: httpx.AsyncClient
    tls: AiaFixer
    emit: Callable[[StageEvent], None] = field(default=_no_emit)
    resolver: Resolver | None = None  # DNS seam for URL-policy checks; tests inject a fake
    # Politeness state for the data layer (R14), per run: request slots and parsed robots.txt
    # per host. Created lazily by data.adapters.base; kept here so each event loop gets its own.
    domain_slots: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    robots: dict[str, Any] = field(default_factory=dict)


@asynccontextmanager
async def open_context(
    home: EngineHome,
    run: RunHandle,
    emit: Callable[[StageEvent], None] | None = None,
) -> AsyncIterator[RunContext]:
    config = load_config(home)
    secrets = load_secrets(home)
    tracer = Tracer(
        run.trace_path,
        run.run_id,
        config.llm.pricing.input_per_m,
        config.llm.pricing.output_per_m,
        config.budgets.run_usd,
        secrets=[
            v.get_secret_value()
            for v in (
                secrets.deepseek_api_key,
                secrets.tavily_api_key,
                secrets.serper_api_key,
                secrets.jina_api_key,
            )
            if v is not None
        ],
        cache_hit_per_m=config.llm.pricing.cache_hit_input_per_m,
    )
    fixer = AiaFixer(home.certs_dir)
    try:
        async with httpx.AsyncClient() as client:
            yield RunContext(
                home=home,
                config=config,
                secrets=secrets,
                run=run,
                tracer=tracer,
                cache=Cache(home.db_path),
                http=client,
                tls=fixer,
                emit=emit or _no_emit,
            )
    finally:
        await fixer.aclose()
