"""`engine memory`: inspect and clear what the research agents remember."""

import json
from typing import Annotated

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from kenya_data_engine.cli.common import get_state, guarded
from kenya_data_engine.cli.ui import console, show_error
from kenya_data_engine.research.memory import SourceMemory, TopicMemory

memory_app = typer.Typer(
    name="memory",
    help="Inspect and clear source memory and topic memory.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@guarded
def list_cmd(
    ctx: typer.Context,
    json_out: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    """List remembered sources (which worked for which need) and covered topics.

    \b
    Examples:
      engine memory list
      engine memory list --json
    """
    state = get_state(ctx)
    state.home.ensure()
    sources = SourceMemory(state.home.db_path).entries()
    topics = TopicMemory(state.home.db_path).entries()
    if json_out:
        typer.echo(json.dumps({"sources": sources, "topics": topics}, indent=2))
        return
    if not sources and not topics:
        console.print("Nothing remembered yet — memory fills as `engine research` finds sources.")
        return
    if sources:
        console.print(Text("Sources that worked (copy a signature to forget it)", style="accent"))
        for e in sources:
            spec = e["spec"]
            where = spec.get("registry_key") or spec.get("url") or spec["via"]
            console.print(Text(e["signature"], style="bold"), soft_wrap=True)
            console.print(
                Text(
                    f"  {spec['publisher']}: {where} · ok {e['successes']} · "
                    f"failed {e['failures']} · verified {e['last_verified'] or '-'}",
                    style="muted",
                ),
                soft_wrap=True,
            )
        console.print()
    if topics:
        table = Table(
            title="Topics covered",
            title_justify="left",
            title_style="accent",
            box=box.ROUNDED,
            border_style="muted",
        )
        for col in ("Date", "Topic", "Dossier"):
            table.add_column(col, overflow="fold")
        for t in topics:
            table.add_row(t["created"], Text(t["title"]), Text(t["dossier_path"]))
        console.print(table)


@guarded
def forget_cmd(
    ctx: typer.Context,
    signature: Annotated[
        str, typer.Argument(help="A signature exactly as `engine memory list` shows it.")
    ],
) -> None:
    """Forget every remembered source for one need signature.

    \b
    Examples:
      engine memory forget "super petrol|nairobi|kes/l|monthly"
    """
    state = get_state(ctx)
    state.home.ensure()
    removed = SourceMemory(state.home.db_path).forget(signature)
    if removed == 0:
        show_error(
            f"nothing remembered under {signature!r}", "copy a signature from `engine memory list`"
        )
        raise typer.Exit(1)
    console.print(f"Forgot {removed} remembered source{'s' if removed != 1 else ''}.")


memory_app.command("list")(list_cmd)
memory_app.command("forget")(forget_cmd)
