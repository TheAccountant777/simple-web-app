"""CLI plumbing: global state, home resolution and the friendly error handler."""

import functools
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from kenya_data_engine.cli.ui import console, err_console, show_error
from kenya_data_engine.config import load_secrets
from kenya_data_engine.errors import EngineError
from kenya_data_engine.home import EngineHome


@dataclass
class State:
    home: EngineHome
    verbose: bool = False
    quiet: bool = False


def get_state(ctx: typer.Context) -> State:
    obj = ctx.obj
    if isinstance(obj, State):
        return obj
    return State(home=EngineHome.resolve(None))  # command invoked without the root callback


def make_state(home: Path | None, verbose: bool, quiet: bool) -> State:
    return State(home=EngineHome.resolve(home), verbose=verbose, quiet=quiet)


def _scrub(text: str, home: EngineHome) -> str:
    try:
        secrets = load_secrets(home)
    except Exception:
        return text
    for value in (
        secrets.deepseek_api_key,
        secrets.tavily_api_key,
        secrets.serper_api_key,
        secrets.jina_api_key,
    ):
        if value is not None and value.get_secret_value():
            text = text.replace(value.get_secret_value(), "***")
    return text


def _traceback(state: State, to_stderr: bool) -> None:
    out = err_console if to_stderr else console
    out.print(_scrub(traceback.format_exc(), state.home), markup=False, style="muted")


def guarded[F: Callable[..., Any]](fn: F) -> F:
    """Turn exceptions into friendly messages: exit 2 for EngineError, 1 otherwise."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx: typer.Context = kwargs["ctx"]  # every guarded command takes `ctx: typer.Context`
        state = get_state(ctx)
        to_stderr = bool(ctx.params.get("json_out"))  # keep stdout pure JSON
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, typer.Abort, typer.BadParameter, typer.TyperException):
            raise
        except EngineError as exc:
            show_error(_scrub(exc.message, state.home), exc.hint, stderr=to_stderr)
            if state.verbose:
                _traceback(state, to_stderr)
            raise typer.Exit(2) from None
        except Exception as exc:
            msg = _scrub(str(exc) or type(exc).__name__, state.home)
            show_error(f"unexpected error: {msg}", "re-run with -v for details", stderr=to_stderr)
            if state.verbose:
                _traceback(state, to_stderr)
            raise typer.Exit(1) from None

    return wrapper  # type: ignore[return-value]


__all__ = ["State", "console", "get_state", "guarded", "make_state"]
