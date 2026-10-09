"""HTML tables via selectolax."""

from importlib.metadata import version

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from kenya_data_engine.data.extract.grid import Locator, RawTable, build_table, clean


def _span(cell: Node, attr: str) -> int:
    try:
        return max(1, min(int(cell.attributes.get(attr) or 1), 50))
    except ValueError:
        return 1


def _grid(table: Node, t: int) -> tuple[list[list[str]], list[list[str]]]:
    """Expand colspan and rowspan so every cell lands in its true column.

    Returns the text grid and its locator grid. A cell copied from a spanning origin carries
    `span:<origin locator>`: the copy is not a measurement and adapters must not read a number
    from it (grid.build_table lifts the mark in the label column).
    """
    grid: dict[tuple[int, int], str] = {}
    locs: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(table.css("tr")):
        c = 0
        for cell in tr.iter():
            if cell.tag not in ("td", "th"):
                continue
            while (r, c) in grid:
                c += 1
            text = clean(cell.text(separator=" "))
            for dr in range(_span(cell, "rowspan")):
                for dc in range(_span(cell, "colspan")):
                    grid[(r + dr, c + dc)] = text
                    origin = f"t{t}/r{r}/c{c}"
                    locs[(r + dr, c + dc)] = origin if dr == dc == 0 else f"span:{origin}"
            c += _span(cell, "colspan")
    if not grid:
        return [], []
    height = max(r for r, _ in grid) + 1
    width = max(c for _, c in grid) + 1
    return (
        [[grid.get((r, c), "") for c in range(width)] for r in range(height)],
        [[locs.get((r, c), f"t{t}/r{r}/c{c}") for c in range(width)] for r in range(height)],
    )


def read_html(content: bytes, locator: Locator) -> list[RawTable]:
    tree = HTMLParser(content.decode("utf-8", errors="replace"))
    tables: list[Node] = []
    for node in tree.css(locator.css or "table"):
        tables.extend([node] if node.tag == "table" else node.css("table"))
    out: list[RawTable] = []
    for t, table in enumerate(tables):
        cells, locs = _grid(table, t)
        built = build_table(cells, locs, locator, f"selectolax@{version('selectolax')}", "html")
        if built:
            out.append(built)
    return out
