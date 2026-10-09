from pathlib import Path

import pytest

from kenya_data_engine.errors import FetchError
from kenya_data_engine.tools.pdf import read_pdf

PDF = Path(__file__).parent / "fixtures" / "pdf" / "sample_table.pdf"
URL = "https://example.co.ke/report.pdf"


async def test_read_pdf_returns_table(respx_mock, ctx):
    respx_mock.get(URL).respond(
        content=PDF.read_bytes(), headers={"content-type": "application/pdf"}
    )
    out = await read_pdf(URL, ctx)
    assert out.url == URL and out.pages[0].number == 1
    assert "CBK rates" in out.pages[0].text
    table = out.pages[0].tables[0]
    assert len(table) == 2 and len(table[0]) == 3
    assert table[1] == ["2025", "9.5", "-0.5"]


async def test_read_pdf_page_selection(respx_mock, ctx):
    respx_mock.get(URL).respond(content=PDF.read_bytes())
    assert (await read_pdf(URL, ctx, pages=[1])).pages[0].number == 1
    assert (await read_pdf(URL, ctx, pages=[5])).pages == []


async def test_read_pdf_invalid_bytes_raises_fetch_error(respx_mock, ctx):
    respx_mock.get(URL).respond(content=b"not a pdf")
    with pytest.raises(FetchError):
        await read_pdf(URL, ctx)
