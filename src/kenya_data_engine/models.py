"""Core data models shared across stages."""

import hashlib
from datetime import datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

SignalKind = Literal["news", "data_release", "policy", "attention", "calendar"]
Category = Literal["economy", "personal_finance", "startups", "business", "law"]


def normalize_url(url: str) -> str:
    """Lowercase the host, drop the fragment and utm_* params, strip a trailing slash."""
    parts = urlsplit(url.strip())
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.startswith("utm_")
        ]
    )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, query, ""))


def signal_id(url: str | None, title: str) -> str:
    key = normalize_url(url) if url else "title:" + title.casefold()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


class Signal(BaseModel):
    id: str
    kind: SignalKind
    title: str
    source: str
    url: str | None
    published_at: datetime | None
    snippet: str = ""
    meta: dict[str, str] = Field(default_factory=dict)


class AdapterError(BaseModel):
    adapter: str
    message: str


class RadarResult(BaseModel):
    signals: list[Signal]
    errors: list[AdapterError]
    collected_at: datetime


class TopicScores(BaseModel):
    data_ability: int = Field(ge=1, le=5)
    wallet_impact: int = Field(ge=1, le=5)
    timeliness: int = Field(ge=1, le=5)
    clarity_gap: int = Field(ge=1, le=5)
    novelty: int = Field(ge=1, le=5)
    justification: dict[str, str]


class Topic(BaseModel):
    id: str
    title: str
    summary: str
    why_now: str
    category: Category
    signal_ids: list[str]
    scores: TopicScores
    final_score: float


class TopicList(BaseModel):
    topics: list[Topic]
    dropped: list[str] = Field(default_factory=list)
    budget_exhausted: bool = False
