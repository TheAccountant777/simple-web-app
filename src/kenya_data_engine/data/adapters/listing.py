"""Listing-page crawler: regex-matched links become items; sheets/PDFs become observations."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from kenya_data_engine.context import RunContext
from kenya_data_engine.data.adapters.base import Discovered, policy_fetch
from kenya_data_engine.data.extract import Locator, extract_tables
from kenya_data_engine.data.models import Observation, Provenance
from kenya_data_engine.data.periods import Period, PeriodType, parse_period
from kenya_data_engine.errors import ExtractError
from kenya_data_engine.tools.numbers import parse_number

if TYPE_CHECKING:
    from kenya_data_engine.data.registry import CatalogEntry


def _pattern(entry: CatalogEntry, name: str, *, required: bool = False) -> re.Pattern[str] | None:
    raw = entry.params.get(name)
    if raw is None:
        if required:
            raise ExtractError(f"{entry.key}: params.{name} is required")
        return None
    try:
        return re.compile(str(raw))
    except re.error as exc:
        raise ExtractError(f"{entry.key}: params.{name} is not a valid regex: {exc}") from exc


def match_period(
    entry: CatalogEntry, texts: list[str | None], hint: PeriodType | None
) -> Period | None:
    """Apply `date_pattern` (group 1 if present, else the whole match) to each text in turn."""
    pattern = _pattern(entry, "date_pattern")
    if pattern is None:
        return None
    for text in texts:
        if not text:
            continue
        if m := pattern.search(text):
            found = m.group(1) if m.groups() else m.group(0)
            if period := parse_period(found, hint):
                return period
    return None


class ListingAdapter:
    kind = "listing"

    async def discover(self, entry: CatalogEntry, ctx: RunContext) -> list[Discovered]:
        url = entry.params.get("url")
        if not isinstance(url, str) or not url:
            raise ExtractError(f"{entry.key}: params.url is required")
        link_re = _pattern(entry, "link_pattern", required=True)
        assert link_re is not None
        res = await policy_fetch(url, ctx, "page")
        tree = LexborHTMLParser(res.content.decode("utf-8", errors="replace"))
        hint = entry.spec.period_type if entry.spec else None
        seen: set[str] = set()
        found: list[Discovered] = []
        for node in tree.css("a[href]"):
            href = (node.attributes.get("href") or "").strip()
            if not href or not link_re.search(href):
                continue
            absolute = urljoin(res.url, href.replace(" ", "%20"))
            if absolute in seen:
                continue
            seen.add(absolute)
            text = " ".join(node.text().split()) or None
            period = match_period(entry, [text, href], hint)
            found.append(
                Discovered(url=absolute, title=text, published=period.start if period else None)
            )
        return found

    async def observations(
        self, entry: CatalogEntry, item: Discovered, content: bytes, sha: str, ctx: RunContext
    ) -> list[Observation]:
        spec = entry.spec
        if spec is None:
            return []
        locator = Locator.model_validate(entry.params.get("locator", {}))
        columns: dict[str, str] = {
            str(k).strip().lower(): str(v) for k, v in entry.params.get("columns", {}).items()
        }
        if not columns:
            raise ExtractError(f"{entry.key}: params.columns is required for series entries")
        tables = await extract_tables(content, locator, ctx.config.data)
        if locator.table_index >= len(tables):
            raise ExtractError(
                f"{entry.key}: table {locator.table_index} not found ({len(tables)} tables)"
            )
        table = tables[locator.table_index]
        roles = [(i, columns.get(h.strip().lower())) for i, h in enumerate(table.header)]
        entity_col = next((i for i, r in roles if r == "entity"), None)
        period_col = next((i for i, r in roles if r == "period"), None)
        value_cols = [(i, r.split(":", 1)[1]) for i, r in roles if r and r.startswith("value:")]
        if not value_cols:
            raise ExtractError(
                f"{entry.key}: none of the mapped columns {sorted(columns)} found in "
                f"headers {table.header}"
            )
        item_period = match_period(entry, [item.title, item.url], spec.period_type)
        now = datetime.now(UTC)
        out: list[Observation] = []
        for r, row in enumerate(table.rows):
            row = row + [""] * (len(table.header) - len(row))
            entity = row[entity_col].strip() if entity_col is not None else "Kenya"
            period = (
                parse_period(row[period_col].strip(), spec.period_type)
                if period_col is not None
                else item_period
            )
            if not entity or period is None:
                continue
            for c, metric in value_cols:
                parsed = parse_number(row[c])
                if parsed is None:
                    continue
                out.append(
                    Observation(
                        series=entry.key,
                        period=period,
                        entity=entity,
                        metric=metric,
                        value=parsed.value,
                        unit=spec.unit,
                        provenance=Provenance(
                            url=item.url,
                            blob_sha256=sha,
                            retrieved_at=now,
                            published=item.published,
                            locator=table.cell_locators[r][c]
                            if c < len(table.cell_locators[r])
                            else f"r{r}/c{c}",
                            extractor=table.extractor,
                        ),
                    )
                )
        return out
