"""`engine gc`: prune old blobs that no stored observation references."""

import re
import time
from datetime import timedelta
from pathlib import Path
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


def _candidates(root: Path, older_than: timedelta, keep: set[str]) -> list[Path]:
    cutoff = time.time() - older_than.total_seconds()
    return [
        f
        for f in root.glob("*/*")
        if f.is_file()
        and not f.name.startswith(".tmp-")
        and f.name not in keep
        and f.stat().st_mtime < cutoff
    ]


@guarded
def gc(
    ctx: typer.Context,
    older_than: Annotated[
        str, typer.Option("--older-than", help="Minimum blob age, e.g. 90d, 12h, 2w.")
    ] = "90d",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Delete blobs older than the cutoff that no stored observation references.

    \b
    Examples:
      engine gc
      engine gc --older-than 30d --yes
    """
    state = get_state(ctx)
    age = parse_age(older_than)
    state.home.ensure()
    blobs = BlobStore(state.home.blobs_dir)
    keep = SeriesStore(state.home.db_path).referenced_blobs()
    doomed = _candidates(blobs.root, age, keep)
    freed = sum(f.stat().st_size for f in doomed)
    if not doomed:
        console.print("[muted]Nothing to prune.[/]")
        return
    if not yes and not typer.confirm(f"Delete {len(doomed)} blob(s), {freed:,} bytes?"):
        raise typer.Exit(1)
    removed = blobs.prune(age, keep)
    console.print(f"Pruned {removed} blob(s), freed {freed:,} bytes.")
