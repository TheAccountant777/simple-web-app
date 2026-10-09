"""`engine tui` (alias `engine browse`): the full-screen engine room."""

import typer

from kenya_data_engine.cli.common import get_state, guarded
from kenya_data_engine.tui.app import EngineRoom


@guarded
def tui(ctx: typer.Context) -> None:
    """Open the engine room: watch runs, read the score maths, inspect LLM calls, test sources.

    \b
    Keys: 1-4 switch tabs, ? help, q quit.
    """
    state = get_state(ctx)
    state.home.ensure()
    EngineRoom(state.home).run()
