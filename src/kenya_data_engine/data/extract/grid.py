"""Shared table shaping: a raw cell grid becomes a RawTable with headers and cell provenance."""

import re
from typing import Literal

from pydantic import BaseModel

from kenya_data_engine.tools.numbers import parse_number


class Locator(BaseModel):
    pages: list[int] = []
    table_index: int = 0
    sheet: str | None = None
    css: str | None = None
    header_rows: int = 1
    columns: dict[str, str] = {}


class RawTable(BaseModel):
    header: list[str]  # flattened multi-row headers joined with " / "
    rows: list[list[str]]  # cell text, footnote rows removed
    cell_locators: list[list[str]]  # same shape as rows; Provenance.locator strings
    extractor: str
    source_kind: Literal["csv", "xlsx", "html", "pdf"]


_FOOTNOTE = re.compile(r"(?:source|note|\*|\d{1,2}\))", re.IGNORECASE)
_WS = re.compile(r"\s+")


def clean(text: object) -> str:
    return "" if text is None else _WS.sub(" ", str(text)).strip()


def _is_footnote(row: list[str]) -> bool:
    first = next((c for c in row if c), "")
    return bool(first) and _FOOTNOTE.match(first) is not None


def _is_header_row(row: list[str]) -> bool:
    cells = [c for c in row if c]
    if not cells:
        return False
    numeric = sum(parse_number(c) is not None for c in cells)
    return numeric * 2 < len(cells)


def _detect_header_rows(rows: list[list[str]]) -> int:
    n = 0
    for row in rows:
        if not _is_header_row(row):
            break
        n += 1
    return max(n, 1) if rows else 0


def _flatten(header_rows: list[list[str]], width: int) -> list[str]:
    filled: list[list[str]] = []
    for i, row in enumerate(header_rows):
        row = list(row)
        if i < len(header_rows) - 1:  # merged cells only span the upper header rows
            for c in range(1, width):
                if not row[c] and row[c - 1]:
                    row[c] = row[c - 1]
        filled.append(row)
    out: list[str] = []
    for c in range(width):
        parts: list[str] = []
        for row in filled:
            if row[c] and (not parts or parts[-1] != row[c]):
                parts.append(row[c])
        out.append(" / ".join(parts))
    return out


def build_table(
    cells: list[list[str]],
    locs: list[list[str]],
    locator: Locator,
    extractor: str,
    kind: Literal["csv", "xlsx", "html", "pdf"],
) -> RawTable | None:
    """Drop blank rows/columns and footnotes, split headers, keep original cell locators."""
    width = max((len(r) for r in cells), default=0)
    pairs = [
        (r + [""] * (width - len(r)), loc + [""] * (width - len(loc)))
        for r, loc in zip(cells, locs, strict=True)
        if any(r)
    ]
    pairs = [(r, loc) for r, loc in pairs if not _is_footnote(r)]
    keep = [c for c in range(width) if any(r[c] for r, _ in pairs)]
    grid = [[r[c] for c in keep] for r, _ in pairs]
    loc_grid = [[loc[c] for c in keep] for _, loc in pairs]
    if not grid:
        return None
    n_head = (
        locator.header_rows
        if "header_rows" in locator.model_fields_set
        else _detect_header_rows(grid)
    )
    n_head = min(n_head, len(grid))
    return RawTable(
        header=_flatten(grid[:n_head], len(keep)) if n_head else [""] * len(keep),
        rows=grid[n_head:],
        cell_locators=loc_grid[n_head:],
        extractor=extractor,
        source_kind=kind,
    )
