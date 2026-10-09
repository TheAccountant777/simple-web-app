import pytest
from pydantic import ValidationError

from kenya_data_engine.research.models import Angle, ChartConcept, DataNeed, ResearchBrief


def _brief(**over):
    base = {
        "topic": "fuel",
        "core_question": "why is fuel dear?",
        "angles": [
            Angle(label="A", thesis="taxes", contrarian=False),
            Angle(label="B", thesis="margins", contrarian=True),
        ],
        "framing_challenge": "what if wrong?",
        "chart_concepts": [ChartConcept(id="c1", relationship="change_over_time", idea="x")],
        "data_needs": [DataNeed(kind="series", question="pump price")],
        "verdict": "supported",
    }
    return ResearchBrief(**{**base, **over})


def test_brief_requires_contrarian():
    with pytest.raises(ValidationError, match="contrarian"):
        _brief(angles=[Angle(label="A", thesis="t", contrarian=False)])


def test_brief_need_count_bounds():
    with pytest.raises(ValidationError):
        _brief(data_needs=[])
    with pytest.raises(ValidationError):
        _brief(data_needs=[DataNeed(kind="fact", question="q")] * 7)
    b = _brief(data_needs=[DataNeed(kind="fact", question=f"q{i}") for i in range(6)])
    assert [n.id for n in b.data_needs] == ["n1", "n2", "n3", "n4", "n5", "n6"]


def test_brief_chart_concept_bounds():
    with pytest.raises(ValidationError):
        _brief(chart_concepts=[])
    with pytest.raises(ValidationError):
        _brief(chart_concepts=[ChartConcept(id="c", relationship="ranking", idea="x")] * 5)
