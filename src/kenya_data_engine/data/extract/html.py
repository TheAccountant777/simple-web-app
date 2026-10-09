"""HTML tables via selectolax."""

from importlib.metadata import version

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from kenya_data_engine.data.extract.grid import Locator, RawTable, build_table, clean


def _grid(table: Node) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.css("tr"):
        row: list[str] = []
        for cell in tr.iter():
            if cell.tag not in ("td", "th"):
                continue
            row.append(clean(cell.text(separator=" ")))
            try:
                span = int(cell.attributes.get("colspan") or 1)
            except ValueError:
                span = 1
            row.extend([""] * (max(1, min(span, 50)) - 1))
        rows.append(row)
    return rows


def read_html(content: bytes, locator: Locator) -> list[RawTable]:
    tree = HTMLParser(content.decode("utf-8", errors="replace"))
    tables: list[Node] = []
    for node in tree.css(locator.css or "table"):
        tables.extend([node] if node.tag == "table" else node.css("table"))
    out: list[RawTable] = []
    for t, table in enumerate(tables):
        cells = _grid(table)
        locs = [[f"t{t}/r{r}/c{c}" for c in range(len(row))] for r, row in enumerate(cells)]
        built = build_table(cells, locs, locator, f"selectolax@{version('selectolax')}", "html")
        if built:
            out.append(built)
    return out
