"""`engine gc`: prune old blobs that no stored observation or recorded reference needs."""

import re
from datetime import timedelta
from typing import Annotated

import typer

from kenya_data_engine.cli.common import get_state, guarded
from kenya_data_engine.cli.ui import console
from kenya_data_engine.data.store import BlobStore, SeriesStore
from kenya_data_engine.errors import EngineError

_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def parse_age(text: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([hdw])", text.strip().lower())
    if not m:
        raise EngineError(f"invalid age `{text}`", hint="use e.g. 90d, 12h or 2w")
    return timedelta(**{_UNITS[m[2]]: int(m[1])})


@guarded
def gc(
    ctx: typer.Context,
    older_than: Annotated[
        str, typer.Option("--older-than", help="Minimum blob age, e.g. 90d, 12h, 2w.")
    ] = "90d",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Delete blobs older than the cutoff that no stored observation or evidence references.

    \b
    Examples:
      engine gc
      engine gc --older-than 30d --yes
    """
    state = get_state(ctx)
    age = parse_age(older_than)
    state.home.ensure()
    blobs = BlobStore(state.home.blobs_dir, state.home.db_path)
    keep = SeriesStore(state.home.db_path).referenced_blobs() | BlobStore.referenced(
        state.home.db_path
    )
    doomed = blobs.prune(age, keep, dry_run=True)
    if not doomed:
        console.print("[muted]Nothing to prune.[/]")
        return
    sizes = {f: f.stat().st_size for f in doomed if f.exists()}
    if not yes and not typer.confirm(
        f"Delete {len(doomed)} blob(s), {sum(sizes.values()):,} bytes?"
    ):
        raise typer.Exit(1)
    removed = blobs.prune(age, keep)
    freed = sum(sizes.get(f, 0) for f in removed)
    console.print(f"Pruned {len(removed)} blob(s), freed {freed:,} bytes.")
