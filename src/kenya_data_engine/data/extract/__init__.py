"""Tiered table extraction: sheets, then HTML, then PDF, each cell carrying its locator."""

import asyncio

import pandas

from kenya_data_engine.config import DataConfig
from kenya_data_engine.data.extract.grid import Locator, RawTable
from kenya_data_engine.data.extract.html import read_html
from kenya_data_engine.data.extract.pdf import read_pdf_tables
from kenya_data_engine.data.extract.sheets import read_csv, read_xlsx
from kenya_data_engine.errors import ExtractError
from kenya_data_engine.tools.urlpolicy import sniff

__all__ = ["Locator", "RawTable", "extract_tables", "table_to_frame"]


def _dispatch(content: bytes, locator: Locator, cfg: DataConfig) -> list[RawTable]:
    kind = sniff(content)
    if kind in ("xlsx", "csv") and len(content) > cfg.max_bytes["sheet"]:
        raise ExtractError(
            f"{kind} too large ({len(content)} > {cfg.max_bytes['sheet']} bytes)",
            hint="Raise data.max_bytes.sheet if this file is legitimate.",
        )
    if kind == "xlsx":
        return read_xlsx(content, locator, cfg.max_bytes["sheet"])
    if kind == "csv":
        return read_csv(content, locator)
    if kind == "html":
        return read_html(content, locator)
    if kind == "pdf":
        return read_pdf_tables(content, locator, cfg.pdf_max_pages)
    if kind == "xls":
        raise ExtractError("legacy .xls not supported", hint="Save the file as .xlsx.")
    raise ExtractError(f"unsupported content type: {kind}")


async def extract_tables(content: bytes, locator: Locator, cfg: DataConfig) -> list[RawTable]:
    """All tables found (CSV/XLSX: one). The caller picks `locator.table_index` among them."""
    try:
        async with asyncio.timeout(cfg.parse_timeout_s):
            return await asyncio.to_thread(_dispatch, content, locator, cfg)
    except TimeoutError as exc:
        raise ExtractError(f"parsing took longer than {cfg.parse_timeout_s}s") from exc


def table_to_frame(t: RawTable) -> pandas.DataFrame:
    """String cells only; numbers are parsed later through tools.numbers."""
    return pandas.DataFrame(t.rows, columns=pandas.Index(t.header), dtype=str)
