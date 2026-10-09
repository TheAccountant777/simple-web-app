"""`engine init`: first-run wizard (home, config files, API keys)."""

import os
import re
from importlib import resources
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.prompt import Prompt

from kenya_data_engine.cli.common import State, get_state, guarded
from kenya_data_engine.cli.doctor import run_doctor
from kenya_data_engine.cli.ui import console, show_error
from kenya_data_engine.config import load_config, load_secrets
from kenya_data_engine.errors import EngineError

# (env var, label, requirement)
KEYS: list[tuple[str, str, str]] = [
    ("DEEPSEEK_API_KEY", "DeepSeek API key", "required"),
    ("TAVILY_API_KEY", "Tavily API key", "recommended"),
    ("SERPER_API_KEY", "Serper API key", "optional"),
    ("JINA_API_KEY", "Jina API key", "optional"),
]
MAX_ATTEMPTS = 3
_PLAIN = re.compile(r"^[A-Za-z0-9_.:/+=@%-]*$")


def _ask(label: str, requirement: str, has_existing: bool) -> str:
    """Prompt for one secret with hidden input. Isolated so tests can replace it."""
    hint = {
        "required": "required",
        "recommended": "recommended, Enter to skip",
        "optional": "optional, Enter to skip",
    }[requirement]
    if has_existing:
        hint = "Enter to keep the saved key"
    return Prompt.ask(f"[accent]{label}[/] [muted]({hint})[/]", password=True, console=console)


def verify_deepseek(base_url: str, key: str) -> tuple[bool, str]:
    """Check the key with `GET {base_url}/models`. Returns (ok, message)."""
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        return False, f"could not reach DeepSeek ({type(exc).__name__})"
    if resp.status_code in (401, 403):
        return False, "DeepSeek rejected this key"
    if resp.status_code != 200:
        return False, f"DeepSeek answered HTTP {resp.status_code}"
    return True, "DeepSeek key accepted"


def _copy_default(name: str, dest: Path, force: bool) -> str:
    existed = dest.exists()
    if existed and not force:
        return "kept existing"
    text = resources.files("kenya_data_engine").joinpath(f"defaults/{name}").read_text("utf-8")
    dest.write_text(text, encoding="utf-8")
    return "overwritten" if existed else "created"


def _format_value(value: str) -> str:
    if _PLAIN.match(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Merge `updates` into the .env file (keeping unrelated lines) with mode 0600."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in pending:
            out.append(f"{m.group(1)}={_format_value(pending.pop(m.group(1)))}")
        else:
            out.append(line)
    out.extend(f"{k}={_format_value(v)}" for k, v in pending.items())
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _gather_keys(
    state: State, non_interactive: bool, verify: bool, base_url: str
) -> dict[str, str]:
    existing = load_secrets(state.home)
    saved = {
        "DEEPSEEK_API_KEY": existing.deepseek_api_key,
        "TAVILY_API_KEY": existing.tavily_api_key,
        "SERPER_API_KEY": existing.serper_api_key,
        "JINA_API_KEY": existing.jina_api_key,
    }
    saved_plain = {k: v.get_secret_value() if v is not None else "" for k, v in saved.items()}
    chosen: dict[str, str] = {}
    for env, label, requirement in KEYS:
        current = saved_plain[env]
        if non_interactive:
            from_env = os.environ.get(env, "").strip()
            value = from_env or current
            changed = bool(from_env)
        else:
            value = _prompt_key(env, label, requirement, current, verify, base_url)
            changed = value != current
        if not value:
            if requirement == "required":
                raise EngineError(
                    f"{env} is required",
                    hint=f"export {env}=... and re-run, or run `engine init` interactively",
                )
            continue
        if non_interactive and env == "DEEPSEEK_API_KEY" and verify:
            ok, msg = verify_deepseek(base_url, value)
            if not ok:
                raise EngineError(msg, hint="check the key, or use --no-verify to skip this check")
        if changed:
            chosen[env] = value
    return chosen


def _prompt_key(
    env: str, label: str, requirement: str, current: str, verify: bool, base_url: str
) -> str:
    attempts = MAX_ATTEMPTS if requirement == "required" else 1
    for attempt in range(1, attempts + 1):
        value = _ask(label, requirement, bool(current)).strip()
        if not value:
            if current or requirement != "required":
                return current
            show_error(f"{label} is required", f"try again ({attempt}/{attempts})")
            continue
        if env == "DEEPSEEK_API_KEY" and verify:
            ok, msg = verify_deepseek(base_url, value)
            if not ok:
                show_error(msg, f"try again ({attempt}/{attempts})")
                continue
            console.print(f"[ok]✓[/] {msg}")
        return value
    raise EngineError(
        f"{label} not accepted after {attempts} attempts",
        hint="check the key at platform.deepseek.com, or use --no-verify to skip the check",
    )


@guarded
def init(
    ctx: typer.Context,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite config.yaml and calendar.yaml.")
    ] = False,
    no_verify: Annotated[
        bool, typer.Option("--no-verify", help="Skip checking the key and running doctor.")
    ] = False,
    non_interactive: Annotated[
        bool, typer.Option("--non-interactive", help="Read keys from the environment; no prompts.")
    ] = False,
) -> None:
    """Set up the engine home: config files and API keys.

    Your keys are stored only in <home>/.env (mode 600), never in runs or logs.

    \b
    Examples:
      engine init
      engine init --force
      DEEPSEEK_API_KEY=sk-... TAVILY_API_KEY=tvly-... engine init --non-interactive
    """
    state = get_state(ctx)
    state.home.ensure()
    console.print(f"[accent]Engine home:[/] {state.home.root}")
    for name, dest in (
        ("config.yaml", state.home.config_path),
        ("calendar.yaml", state.home.calendar_path),
    ):
        result = _copy_default(name, dest, force)
        mark = "[muted]•[/]" if result == "kept existing" else "[ok]✓[/]"
        console.print(f"{mark} {name}: {result}")

    base_url = load_config(state.home).llm.base_url
    if not non_interactive:
        console.print("\n[muted]Paste your keys; input is hidden.[/]")
    verify = not no_verify
    updates = _gather_keys(state, non_interactive, verify, base_url)
    write_env(state.home.env_path, updates)
    console.print(f"[ok]✓[/] .env: saved ({len(updates)} updated, mode 600)")

    if no_verify:
        console.print("\n[muted]Skipped verification.[/] Next: [accent]engine doctor[/]")
        return
    console.print("\n[bold]Checking your setup…[/]")
    code = run_doctor(state)
    if code:
        console.print("\n[warn]Fix the failures above, then run `engine doctor` again.[/]")
        raise typer.Exit(code)
    console.print("\nNext: [accent]engine run[/]")
