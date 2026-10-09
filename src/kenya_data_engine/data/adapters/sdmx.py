"""Generic SDMX-JSON 2.1 data adapter (the IMF endpoint is verified by `engine catalog probe`)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, ClassVar

from kenya_data_engine.context import RunContext
from kenya_data_engine.data.adapters.base import Discovered
from kenya_data_engine.data.models import Observation, Provenance
from kenya_data_engine.data.periods import parse_period
from kenya_data_engine.errors import ExtractError

if TYPE_CHECKING:
    from kenya_data_engine.data.registry import CatalogEntry


def _param(entry: CatalogEntry, name: str) -> str:
    value = entry.params.get(name)
    if not isinstance(value, str) or not value:
        raise ExtractError(f"{entry.key}: params.{name} is required")
    return value


class SdmxAdapter:
    kind = "sdmx"
    headers: ClassVar[dict[str, str]] = {"Accept": "application/json"}

    async def discover(self, entry: CatalogEntry, ctx: RunContext) -> list[Discovered]:
        base = _param(entry, "base_url").rstrip("/")
        start = str(entry.params.get("start_period", "2000"))
        url = f"{base}/data/{_param(entry, 'flow')}/{_param(entry, 'key')}?startPeriod={start}"
        return [Discovered(url=url, title=entry.title)]

    async def observations(
        self, entry: CatalogEntry, item: Discovered, content: bytes, sha: str, ctx: RunContext
    ) -> list[Observation]:
        spec = entry.spec
        if spec is None:
            return []
        try:
            doc: Any = json.loads(content)
            body = doc.get("data", doc)
            datasets = body["dataSets"]
            structure = body.get("structure") or doc["structure"]
            periods = [v["id"] for v in structure["dimensions"]["observation"][0]["values"]]
            series: dict[str, Any] = datasets[0]["series"]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ExtractError(f"{entry.key}: unexpected SDMX-JSON response shape") from exc
        default_entity = str(entry.params.get("entity", "Kenya"))
        dims: list[Any] = (structure.get("dimensions") or {}).get("series") or []

        def entity_of(skey: str) -> str:
            if len(series) == 1:
                return default_entity
            try:
                ids = [dims[i]["values"][int(part)]["id"] for i, part in enumerate(skey.split(":"))]
            except (IndexError, KeyError, ValueError, TypeError):
                return skey
            return ".".join(str(x) for x in ids)

        now = datetime.now(UTC)
        out: list[Observation] = []
        for skey, sval in series.items():
            entity = entity_of(skey)
            for idx, cell in sval.get("observations", {}).items():
                raw = cell[0] if cell else None
                if raw is None:
                    continue
                label = periods[int(idx)]
                period = parse_period(str(label), spec.period_type)
                if period is None:
                    raise ExtractError(f"{entry.key}: cannot parse period {label!r}")
                try:
                    value = Decimal(str(raw))
                except InvalidOperation as exc:
                    raise ExtractError(f"{entry.key}: non-numeric value {raw!r}") from exc
                out.append(
                    Observation(
                        series=entry.key,
                        period=period,
                        entity=entity,
                        metric=spec.metric,
                        value=value,
                        unit=spec.unit,
                        provenance=Provenance(
                            url=item.final_url or item.url,
                            blob_sha256=sha,
                            retrieved_at=item.retrieved_at or now,
                            locator=f"sdmx:{skey}/{label}",
                            extractor="sdmx-json@2.1",
                        ),
                    )
                )
        return out
