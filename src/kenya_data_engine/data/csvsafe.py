"""CSV output that spreadsheets cannot turn into formulas (CSV injection)."""

import csv
import io
import re
from collections.abc import Iterable, Sequence

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def safe_cell(cell: object) -> str:
    """Prefix a text cell that starts with a formula trigger with `'`; real numbers stay as is."""
    text = str(cell)
    if text.startswith(_TRIGGERS) and not _NUMBER.fullmatch(text):
        return "'" + text
    return text


def write_csv(rows: Iterable[Sequence[object]], header: Sequence[object]) -> str:
    """Header plus rows as CSV text (LF line ends), every cell made formula-safe."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([safe_cell(c) for c in header])
    for row in rows:
        writer.writerow([safe_cell(c) for c in row])
    return buf.getvalue()
