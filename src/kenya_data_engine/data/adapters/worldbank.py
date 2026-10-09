"""World Bank v2 API: annual indicator values for Kenya."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from kenya_data_engine.context import RunContext
from kenya_data_engine.data.adapters.base import Discovered
from kenya_data_engine.data.models import Observation, Provenance
from kenya_data_engine.data.periods import year
from kenya_data_engine.errors import ExtractError

if TYPE_CHECKING:
    from kenya_data_engine.data.registry import CatalogEntry

API = "https://api.worldbank.org"


def _indicator(entry: CatalogEntry) -> str:
    indicator = entry.params.get("indicator")
    if not isinstance(indicator, str) or not indicator:
        raise ExtractError(f"{entry.key}: params.indicator is required")
    return indicator


class WorldBankAdapter:
    kind = "worldbank"

    async def discover(self, entry: CatalogEntry, ctx: RunContext) -> list[Discovered]:
        url = f"{API}/v2/country/KEN/indicator/{_indicator(entry)}?format=json&per_page=1000"
        return [Discovered(url=url, title=entry.title)]

    async def observations(
        self, entry: CatalogEntry, item: Discovered, content: bytes, sha: str, ctx: RunContext
    ) -> list[Observation]:
        spec = entry.spec
        if spec is None:
            return []
        indicator = _indicator(entry)
        try:
            payload: Any = json.loads(content, parse_float=Decimal)
            meta = payload[0]
            if isinstance(meta, dict) and meta.get("message"):
                raise ExtractError(f"{entry.key}: World Bank error: {meta['message']}")
            if int(meta.get("pages", 1)) > 1:
                raise ExtractError(f"{entry.key}: response has {meta['pages']} pages (truncated)")
            rows = payload[1] or []
        except (ValueError, IndexError, TypeError, KeyError, AttributeError) as exc:
            raise ExtractError(f"{entry.key}: unexpected World Bank response shape") from exc
        now = datetime.now(UTC)
        out: list[Observation] = []
        for row in rows:
            value = row.get("value")
            if value is None:
                continue
            date_text = str(row["date"])
            out.append(
                Observation(
                    series=entry.key,
                    period=year(int(date_text)),
                    entity="Kenya",
                    metric=spec.metric,
                    value=Decimal(str(value)),
                    unit=spec.unit,
                    provenance=Provenance(
                        url=item.final_url or item.url,
                        blob_sha256=sha,
                        retrieved_at=item.retrieved_at or now,
                        locator=f"api:/v2/country/KEN/indicator/{indicator}#{date_text}",
                        extractor="worldbank-api@v2",
                    ),
                )
            )
        return out
