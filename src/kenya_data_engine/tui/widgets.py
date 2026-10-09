"""Shared TUI pieces: palette, scrubbing, the help modal and the LLM inspector."""

import json
from collections.abc import Callable
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from kenya_data_engine.config import load_secrets
from kenya_data_engine.home import EngineHome
from kenya_data_engine.trace import scrub_values

# A calm palette: one soft accent, muted greys, and three status colours.
INK = "#c9ced8"
MUTED = "#7d8594"
ACCENT = "#7aa2c4"
GOOD = "#8fbf9f"
WARN = "#d9b26f"
BAD = "#d98282"

MAX_BLOCK_CHARS = 12_000

Scrub = Callable[[str], str]


def make_scrubber(home: EngineHome) -> Scrub:
    """A function masking the home's secret values; every string shown goes through it."""
    try:
        secrets = load_secrets(home)
        values = [
            v.get_secret_value()
            for v in (
                secrets.deepseek_api_key,
                secrets.tavily_api_key,
                secrets.serper_api_key,
                secrets.jina_api_key,
            )
            if v is not None and v.get_secret_value()
        ]
    except Exception:  # an unreadable .env must not stop the viewer
        values = []
    values.sort(key=len, reverse=True)

    def scrub(text: str) -> str:
        out: str = scrub_values(text, values)
        return out

    return scrub


def score_bar(score: int) -> str:
    """Five cells, filled to `score` (1-5)."""
    n = max(0, min(5, score))
    return "█" * n + "░" * (5 - n)


def status_mark(ok: bool) -> Text:
    return Text("✓", style=GOOD) if ok else Text("⚠", style=WARN)


# ---- LLM exchange rendering -------------------------------------------------------------


def _clip(text: str) -> str:
    if len(text) <= MAX_BLOCK_CHARS:
        return text
    return text[:MAX_BLOCK_CHARS] + f"\n… {len(text) - MAX_BLOCK_CHARS:,} more characters"


def _block(label: str, body: str, colour: str, scrub: Scrub) -> Panel:
    return Panel(
        Text(_clip(scrub(body))),
        title=Text(label, style=f"bold {colour}"),
        title_align="left",
        border_style=colour,
        padding=(0, 1),
    )


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def message_blocks(messages: list[Any], scrub: Scrub) -> list[Panel]:
    """Role-labelled blocks for a captured pydantic-ai message list."""
    blocks: list[Panel] = []
    seen_instructions: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        instructions = message.get("instructions")
        if isinstance(instructions, str) and instructions and instructions not in seen_instructions:
            seen_instructions.add(instructions)
            blocks.append(_block("INSTRUCTIONS", instructions, MUTED, scrub))
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            kind = part.get("part_kind", "")
            if kind == "system-prompt":
                blocks.append(_block("SYSTEM", _as_text(part.get("content")), MUTED, scrub))
            elif kind == "user-prompt":
                blocks.append(_block("USER", _as_text(part.get("content")), ACCENT, scrub))
            elif kind == "text":
                blocks.append(_block("MODEL", _as_text(part.get("content")), GOOD, scrub))
            elif kind == "tool-call":
                label = f"MODEL · tool call {part.get('tool_name', '?')}"
                blocks.append(_block(label, _as_text(part.get("args")), GOOD, scrub))
            elif kind == "tool-return":
                label = f"TOOL RETURN · {part.get('tool_name', '?')}"
                blocks.append(_block(label, _as_text(part.get("content")), WARN, scrub))
            elif kind == "retry-prompt":
                blocks.append(_block("RETRY", _as_text(part.get("content")), WARN, scrub))
            else:
                blocks.append(_block(str(kind or "PART").upper(), _as_text(part), MUTED, scrub))
    return blocks


def output_json(output: Any, scrub: Scrub) -> str:
    return scrub(json.dumps(output, ensure_ascii=False, indent=2, default=str))


def render_exchange(ex: "Any", scrub: Scrub) -> RenderableType:
    """Everything the inspector shows for one exchange (an `runs.Exchange`)."""
    ok = ex.status == "ok"
    head = Text()
    head.append(f"{ex.stage} · {ex.name}", style=f"bold {INK}")
    head.append("   ")
    head.append("✓ ok" if ok else "✗ failed", style=GOOD if ok else BAD)
    meta = Text(style=MUTED)
    settings = ", ".join(f"{k}={v}" for k, v in ex.settings.items() if v not in (None, {}))
    meta.append(f"model {ex.model}" + (f" · {settings}" if settings else "") + "\n")
    tin, tout = ex.usage.get("input_tokens", 0), ex.usage.get("output_tokens", 0)
    meta.append(
        f"tokens {tin:,} in → {tout:,} out · ${ex.cost_usd:.4f} · {ex.latency_ms / 1000:.2f}s"
        f"\n{ex.file}"
    )
    parts: list[RenderableType] = [head, meta, Text("")]
    if ex.error:
        parts.append(_block("ERROR", ex.error, BAD, scrub))
    parts.extend(message_blocks(ex.messages, scrub))
    parts.append(
        Panel(
            Syntax(
                _clip(output_json(ex.output, scrub)),
                "json",
                theme="ansi_dark",
                word_wrap=True,
                background_color="default",
            ),
            title=Text("PARSED OUTPUT", style=f"bold {ACCENT}"),
            title_align="left",
            border_style=ACCENT,
            padding=(0, 1),
        )
    )
    return Group(*parts)


class InspectorScreen(ModalScreen[None]):
    """Scrollable view of one captured LLM exchange."""

    AUTO_FOCUS = "VerticalScroll"
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close", show=False),
        Binding("c", "copy", "Copy output"),
    ]

    def __init__(self, exchange: Any, scrub: Scrub) -> None:
        super().__init__()
        self.exchange = exchange
        self.scrub = scrub

    def compose(self) -> ComposeResult:
        with Vertical(id="inspector"):
            with VerticalScroll():
                yield Static(render_exchange(self.exchange, self.scrub), id="inspector-body")
            yield Static(
                "[b]esc[/] close   [b]c[/] copy output JSON   [b]↑↓ pgup pgdn[/] scroll",
                id="inspector-keys",
            )

    def action_copy(self) -> None:
        self.app.copy_to_clipboard(output_json(self.exchange.output, self.scrub))
        self.notify("Output JSON copied", timeout=2)


HELP = """\
[b]Tabs[/]
  1 Live   2 Runs   3 Sources

[b]Runs[/]
  tab / shift+tab   move between panes
  ↑ ↓               choose a run, topic, signal or exchange
  o                 open the highlighted signal's URL
  i  or  enter      inspect the highlighted LLM exchange

[b]Sources[/]
  /  filter    t  test selected    T  test all    e  show config

[b]Anywhere[/]
  ?  this help     q  quit
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("question_mark", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Static(HELP)
            yield Static("[dim]esc to close[/]", id="help-keys")
