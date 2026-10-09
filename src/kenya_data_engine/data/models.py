"""Data-layer models: observations with cell-level provenance."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from kenya_data_engine.data.periods import Period, PeriodType


class Provenance(BaseModel):
    url: str
    blob_sha256: str
    retrieved_at: datetime
    published: date | None = None
    locator: str  # "p3/t0/r5/c2" (pdf) · "Sheet1!B7" (xlsx) · "t0/r5/c2" (html/csv) · "api:<path>"
    extractor: str  # "pdfplumber@0.11.4", "openpyxl@3.1.5", "worldbank-api@v2"


class Observation(BaseModel):
    series: str
    period: Period
    entity: str
    metric: str
    value: Decimal
    unit: str
    provenance: Provenance


class StoredObservation(Observation):
    vintage: int
    revised: bool  # a different value exists in an earlier vintage


class SeriesSpec(BaseModel):
    """Declared per registry series; drives the table checks."""

    key: str
    metric: str
    unit: str
    period_type: PeriodType
    entities: list[str] = []  # expected entities (e.g. towns); empty = any
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    max_step_pct: Decimal | None = None  # continuity: |change| vs latest stored value
    totals: list[tuple[str, list[str]]] = []  # (total entity, component entities)
    total_tolerance: Decimal = Decimal("0.5")


class CheckReport(BaseModel):
    status: Literal["accepted", "quarantined"]
    failures: list[str]
    warnings: list[str]
    checked: int
