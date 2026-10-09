"""Typer root application."""

from pathlib import Path
from typing import Annotated

import typer

from kenya_data_engine import __version__
from kenya_data_engine.cli import compare, doctor, init, report, run, stage, tui
from kenya_data_engine.cli import eval as eval_cmd
from kenya_data_engine.cli import research as research_cmd
from kenya_data_engine.cli.catalog import catalog_app
from kenya_data_engine.cli.common import make_state
from kenya_data_engine.cli.data import data_app
from kenya_data_engine.cli.dossiers import dossiers_app
from kenya_data_engine.cli.gc import gc
from kenya_data_engine.cli.memory import memory_app
from kenya_data_engine.cli.sources import sources_app

app = typer.Typer(
    name="engine",
    epilog="Start with `engine init`, check with `engine doctor`, then `engine run` and "
    "`engine research 1`.",
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
      engine tui             the engine room: runs, score maths, LLM calls, sources
      engine report          speed, cost and reliability of past runs
      engine compare         which topics keep coming back across runs
      engine research 1      research the top topic into a dossier (`--plan-only` is cheap)
      engine dossiers list   finished dossiers; `dossiers show latest` reads one
      engine memory list     what the research agents remember; `memory forget <signature>`
      engine eval            run the acceptance scenarios and compare with the last run
      engine stage radar     just collect signals
      engine data list       the series warehouse; `data fetch <key>`, `data show <key>`
      engine catalog probe   live-check catalog sources (`--save-samples DIR`)
      engine gc              prune unreferenced blobs
    """
    ctx.obj = make_state(home, verbose, quiet)


app.command("run")(run.run)
app.command("stage")(stage.stage)
app.command("research")(research_cmd.research_cmd)
app.command("eval")(eval_cmd.eval_cmd)
app.command("report")(report.report)
app.command("compare")(compare.compare)
app.command("tui")(tui.tui)
app.command("browse", help="Alias for `engine tui`.")(tui.tui)
app.command("init")(init.init)
app.command("doctor")(doctor.doctor)
app.command("gc")(gc)
app.add_typer(sources_app)
app.add_typer(data_app)
app.add_typer(dossiers_app)
app.add_typer(memory_app)
app.add_typer(catalog_app)
