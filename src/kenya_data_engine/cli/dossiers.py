"""`engine dossiers`: list finished dossiers and read one."""

import json
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from kenya_data_engine.cli.common import get_state, guarded
from kenya_data_engine.cli.ui import console
from kenya_data_engine.errors import EngineError
from kenya_data_engine.home import EngineHome
from kenya_data_engine.research.dossier import DossierInfo, list_dossiers

dossiers_app = typer.Typer(
    name="dossiers",
    help="List research dossiers and read their README.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

_VERDICT = {"supported": "ok", "reframed": "warn", "reject": "fail"}


def resolve_dossier(ref: str, home: EngineHome) -> Path:
    """A path, `latest`, `NN-slug` or `DATE/NN-slug`, resolved to a dossier folder."""
    direct = Path(ref).expanduser()
    if (direct / "README.md").is_file():
        return direct
    found = list_dossiers(home.briefs_dir)
    if ref == "latest":
        if found:
            return found[0].path
        raise EngineError("no dossiers yet", hint="run `engine research <topic>`")
    for d in found:  # newest first
        if ref in (d.path.name, f"{d.date.isoformat()}/{d.path.name}"):
            return d.path
    raise EngineError(
        f"no dossier matches {ref!r}", hint="see `engine dossiers list` for the folder names"
    )


def _row(d: DossierInfo) -> dict[str, object]:
    return {
        "date": d.date.isoformat(),
        "nn": d.nn,
        "slug": d.slug,
        "topic": d.topic,
        "verdict": d.verdict,
        "facts": d.facts,
        "cost_usd": d.usd,
        "path": str(d.path),
    }


@guarded
def list_cmd(
    ctx: typer.Context,
    json_out: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    """List dossiers, newest first: date, number, slug, verdict, facts and cost.

    \b
    Examples:
      engine dossiers list
      engine dossiers list --json
    """
    state = get_state(ctx)
    found = list_dossiers(state.home.briefs_dir)
    if json_out:
        typer.echo(json.dumps([_row(d) for d in found], indent=2))
        return
    if not found:
        console.print("No dossiers yet — run `engine research <topic>`.")
        return
    table = Table(box=box.ROUNDED, header_style="bold", border_style="muted")
    for col in ("Date", "NN", "Slug", "Verdict", "Facts", "Cost"):
        table.add_column(col, justify="right" if col in ("NN", "Facts", "Cost") else "left")
    for d in found:
        table.add_row(
            d.date.isoformat(),
            f"{d.nn:02d}",
            Text(d.slug, overflow="fold"),
            Text(d.verdict, style=_VERDICT.get(d.verdict, "")),
            str(d.facts),
            f"${d.usd:.4f}",
        )
    console.print(table)


@guarded
def show_cmd(
    ctx: typer.Context,
    dossier: Annotated[
        str, typer.Argument(metavar="DOSSIER", help="A folder path, `NN-slug` or `latest`.")
    ],
) -> None:
    """Print a dossier's README.

    \b
    Examples:
      engine dossiers show latest
      engine dossiers show 02-kenya-fuel-prices
    """
    state = get_state(ctx)
    path = resolve_dossier(dossier, state.home)
    console.print(Text(str(path), style="muted"))
    console.print(Markdown((path / "README.md").read_text(encoding="utf-8")))


dossiers_app.command("list")(list_cmd)
dossiers_app.command("show")(show_cmd)
