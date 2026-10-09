"""Read text and tables from a PDF."""

import asyncio
import io

import pdfplumber
from pydantic import BaseModel

from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import FetchError
from kenya_data_engine.http import fetch


class PdfPage(BaseModel):
    number: int
    text: str
    tables: list[list[list[str | None]]]


class PdfText(BaseModel):
    url: str
    pages: list[PdfPage]


def _parse(content: bytes, wanted: list[int] | None) -> list[PdfPage]:
    out: list[PdfPage] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            if wanted is not None and number not in wanted:
                continue
            out.append(
                PdfPage(
                    number=number,
                    text=page.extract_text() or "",
                    tables=page.extract_tables(),
                )
            )
    return out


async def read_pdf(url: str, ctx: RunContext, pages: list[int] | None = None) -> PdfText:
    async with ctx.tracer.span("tools", "tool", "read_pdf"):
        res = await fetch(
            url,
            client=ctx.http,
            cache=ctx.cache,
            ttl_hours=ctx.config.cache_ttl_hours,
            tracer=ctx.tracer,
        )
        try:
            parsed = await asyncio.to_thread(_parse, res.content, pages)
        except Exception as exc:
            raise FetchError(f"could not parse PDF at {url}", hint=str(exc)) from exc
        return PdfText(url=url, pages=parsed)
