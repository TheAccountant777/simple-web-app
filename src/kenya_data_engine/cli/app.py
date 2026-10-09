"""Typer root application."""

from pathlib import Path
from typing import Annotated

import typer

from kenya_data_engine import __version__
from kenya_data_engine.cli import doctor, init, run, stage
from kenya_data_engine.cli.common import make_state
from kenya_data_engine.cli.sources import sources_app

app = typer.Typer(
    name="engine",
    epilog="Start with `engine init`, check with `engine doctor`, then `engine run`.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"engine {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    home: Annotated[
        Path | None,
        typer.Option(
            "--home", help="Engine home directory (default: $ENGINE_HOME or ~/.kenya-data-engine)."
        ),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show tracebacks and extra detail.")
    ] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Hide progress and the run summary.")
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Kenya Data Engine: find and rank Kenyan data-journalism topics.

    Collects Kenyan signals, scores topics with DeepSeek, prints a ranked list.

    \b
    Examples:
      engine init            set up keys and config
      engine doctor          check keys, LLM, search and sources
      engine sources list    audit sources; `sources test <name>` checks one live
      engine run --top 3     full pipeline
      engine stage radar     just collect signals
    """
    ctx.obj = make_state(home, verbose, quiet)


app.command("run")(run.run)
app.command("stage")(stage.stage)
app.command("init")(init.init)
app.command("doctor")(doctor.doctor)
app.add_typer(sources_app)
