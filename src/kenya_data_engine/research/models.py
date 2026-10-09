"""Typed shapes shared by every research stage (spec section 4)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from kenya_data_engine.data.extract.grid import Locator

Relationship = Literal[
    "change_over_time",
    "ranking",
    "part_to_whole",
    "deviation",
    "correlation",
    "distribution",
    "magnitude",
    "spatial",
    "flow",
]
ClaimType = Literal[
    "price", "rate", "statistic", "annual", "legal_status", "event", "forecast", "other"
]
LegalStage = Literal["proposed", "bill", "passed", "assented", "gazetted", "in_force"]


class Angle(BaseModel):
    label: str
    thesis: str
    contrarian: bool


class ChartConcept(BaseModel):
    id: str
    relationship: Relationship
    idea: str
    needs: list[str] = []


class DataNeed(BaseModel):
    id: str = ""  # assigned by code (n1, n2, ...), never by the model
    kind: Literal["series", "fact"]
    question: str
    metric: str | None = None
    entities: list[str] = []
    unit: str | None = None
    frequency: Literal["daily", "weekly", "monthly", "quarterly", "annual", "cycle", "none"] = (
        "none"
    )
    period_start: date | None = None
    period_end: date | None = None
    min_points: int = 1
    priority: Literal[1, 2, 3] = 2
    series_hint: str | None = None
    chart_concepts: list[str] = []
    publishers: list[str] = []  # preferred domains


class ResearchBrief(BaseModel):
    topic: str
    core_question: str
    angles: list[Angle]
    framing_challenge: str
    chart_concepts: list[ChartConcept]
    data_needs: list[DataNeed]
    verdict: Literal["supported", "reframed", "reject"]
    verdict_reasons: list[str] = []
    reframe: str | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "ResearchBrief":
        if not 1 <= len(self.data_needs) <= 6:
            raise ValueError(f"data_needs must hold 1-6 needs, got {len(self.data_needs)}")
        if not any(a.contrarian for a in self.angles):
            raise ValueError("angles must include at least one contrarian angle")
        if not 1 <= len(self.chart_concepts) <= 4:
            raise ValueError(
                f"chart_concepts must hold 1-4 concepts, got {len(self.chart_concepts)}"
            )
        for i, need in enumerate(self.data_needs, start=1):
            if not need.id:
                need.id = f"n{i}"
        return self


class DataSourceSpec(BaseModel):
    need: str
    via: Literal["registry", "file", "html_table", "page_text"]
    registry_key: str | None = None
    url: str | None = None
    locator: Locator | None = None
    publisher: str
    why: str
    expected_period: str | None = None


class TextEvidence(BaseModel):
    id: str
    label: str
    url: str
    final_url: str
    tier: int
    publisher: str
    published: date | None = None
    retrieved_at: datetime
    blob_sha256: str
    text_path: str
    title: str | None = None


class FigureRef(BaseModel):
    label: str
    figure_id: str
    series: str
    entity: str
    period_label: str
    generic: bool
    tier: int
    published: date | None = None


class CandidateClaim(BaseModel):
    """What the claim writer proposes; labels are as the model sees them."""

    text_template: str
    quote: str | None = None
    evidence: str | None = None
    figures: list[str] = []
    claim_type: ClaimType
    entity: str | None = None
    metric: str | None = None
    period: str | None = None
    legal_stage: LegalStage | None = None
    legal_date: date | None = None
    names_person: bool = False
    alleges_wrongdoing: bool = False


class Claim(BaseModel):
    id: str
    text: str
    text_template: str
    status: Literal["fact", "inference", "speculation", "refused"]
    reasons: list[str] = []
    evidence_id: str | None = None
    figure_ids: list[str] = []
    quote: str | None = None
    quote_grounded: bool = False
    numbers_ok: bool = False
    entity_period_ok: bool = False
    entailment: Literal["yes", "partial", "no", "not_run"] = "not_run"
    tier: int = 4
    claim_type: ClaimType = "other"
    entity: str | None = None
    metric: str | None = None
    period: str | None = None
    legal_stage: LegalStage | None = None
    legal_date: date | None = None
    sensitive: bool = False
    stale: bool = False
    conflicts: list[str] = []


class Conflict(BaseModel):
    id: str
    claim_ids: list[str]
    metric: str | None = None
    entity: str | None = None
    period: str | None = None
    values: list[str] = []
    resolution: Literal["prefer_higher_tier", "prefer_newer", "unresolved"]
    kept: str | None = None


class NeedStatus(BaseModel):
    need_id: str
    status: Literal["satisfied", "partial", "not_found"]
    points: int = 0
    evidence_ids: list[str] = []
    figure_ids: list[str] = []
    series_keys: list[str] = []
    notes: list[str] = []
    round: int = 1


class Scorecard(BaseModel):
    preset: str
    usd_spent: float
    usd_cap: float
    credits_spent: int
    credits_cap: int
    seconds: float
    facts: int = 0
    inferences: int = 0
    speculation: int = 0
    refused: int = 0
    facts_per_usd: float | None = None
    searches_per_satisfied: float | None = None
    registry_hits: int = 0
    memory_hits: int = 0
    cache_hits: int = 0
    needs_total: int = 0
    needs_satisfied: int = 0
    gap_rate: float = 0.0
    stopped_because: str = ""


class DossierMeta(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    topic: str
    created_at: datetime
    engine_version: str
    git_sha: str | None = None
    config_hash: str
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    model: str
    path: str
