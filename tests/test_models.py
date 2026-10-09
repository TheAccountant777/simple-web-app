from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from kenya_data_engine.models import (
    RadarResult,
    Signal,
    Topic,
    TopicList,
    TopicScores,
    normalize_url,
    signal_id,
)


def test_signal_id_stable_across_tracking_params():
    a = signal_id("https://X.com/a/?utm_source=t#top", "T")
    assert a == signal_id("https://x.com/a", "other title") and len(a) == 12


def test_signal_id_without_url_uses_casefolded_title():
    assert signal_id(None, "Fuel PRICES") == signal_id(None, "fuel prices")
    assert signal_id(None, "a") != signal_id(None, "b")


def test_normalize_url_keeps_other_params_and_path():
    assert normalize_url("https://Ex.com/p/?utm_medium=x&id=3#f") == "https://ex.com/p?id=3"


def scores(**over):
    base = dict(
        data_ability=3, wallet_impact=3, timeliness=3, clarity_gap=3, novelty=3, justification={}
    )
    return TopicScores(**{**base, **over})


def test_scores_reject_out_of_range():
    with pytest.raises(ValidationError):
        scores(data_ability=6)
    with pytest.raises(ValidationError):
        scores(novelty=0)


def test_topic_list_defaults_and_topic_roundtrip():
    t = Topic(
        id="t1",
        title="T",
        summary="S",
        why_now="W",
        category="economy",
        signal_ids=["a"],
        scores=scores(),
        final_score=3.0,
    )
    tl = TopicList(topics=[t])
    assert tl.dropped == [] and tl.budget_exhausted is False
    assert TopicList.model_validate_json(tl.model_dump_json()) == tl


def test_topic_rejects_unknown_category():
    with pytest.raises(ValidationError):
        Topic(
            id="t",
            title="T",
            summary="S",
            why_now="W",
            category="sports",  # type: ignore[arg-type]
            signal_ids=[],
            scores=scores(),
            final_score=1.0,
        )


def test_signal_defaults():
    s = Signal(id="x", kind="news", title="T", source="s", url=None, published_at=None)
    assert s.snippet == "" and s.meta == {}
    r = RadarResult(signals=[s], errors=[], collected_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert r.signals[0] is s
