"""PDF tables via pdfplumber."""

import io

import pdfplumber

from kenya_data_engine.data.extract.grid import Locator, RawTable, build_table, clean
from kenya_data_engine.errors import ExtractError


def read_pdf_tables(content: bytes, locator: Locator, max_pages: int) -> list[RawTable]:
    out: list[RawTable] = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            total = len(pdf.pages)
            wanted = locator.pages or list(range(1, total + 1))
            if (not locator.pages and total > max_pages) or len(wanted) > max_pages:
                raise ExtractError(
                    f"pdf has {len(wanted)} pages to read, over the cap of {max_pages}",
                    hint="Set locator.pages to the pages that hold the table.",
                )
            for number in wanted:
                if not 1 <= number <= total:
                    raise ExtractError(f"page {number} is out of range (1-{total})")
                for t, raw in enumerate(pdf.pages[number - 1].extract_tables()):
                    cells = [[clean(c) for c in row] for row in raw]
                    locs = [
                        [f"p{number}/t{t}/r{r}/c{c}" for c in range(len(row))]
                        for r, row in enumerate(cells)
                    ]
                    built = build_table(
                        cells, locs, locator, f"pdfplumber@{pdfplumber.__version__}", "pdf"
                    )
                    if built:
                        out.append(built)
    except ExtractError:
        raise
    except Exception as exc:  # pdfminer raises many types on bad input
        raise ExtractError(f"unreadable pdf: {exc}") from exc
    return out
