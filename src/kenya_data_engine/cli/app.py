"""Typer root application."""

import typer

from kenya_data_engine import __version__

app = typer.Typer(help="Kenya Data Engine", no_args_is_help=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"engine {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """Kenya Data Engine."""
