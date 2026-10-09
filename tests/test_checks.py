from datetime import UTC, datetime
from decimal import Decimal

from kenya_data_engine.data.checks import check_observations
from kenya_data_engine.data.models import Observation, Provenance, SeriesSpec, StoredObservation
from kenya_data_engine.data.periods import month

PROV = Provenance(
    url="https://x/y",
    blob_sha256="a" * 64,
    retrieved_at=datetime(2026, 10, 1, tzinfo=UTC),
    published=None,
    locator="t0/r1/c1",
    extractor="csv@stdlib",
)


def _o(entity: str, value: str, m: int = 9, unit: str = "KES/L") -> Observation:
    return Observation(
        series="s",
        period=month(2026, m),
        entity=entity,
        metric="super",
        value=Decimal(value),
        unit=unit,
        provenance=PROV,
    )


def _stored(o: Observation) -> StoredObservation:
    return StoredObservation(**o.model_dump(), vintage=1, revised=False)


def _spec(**kw: object) -> SeriesSpec:
    base: dict[str, object] = {
        "key": "s",
        "metric": "super",
        "unit": "KES/L",
        "period_type": "month",
        "entities": ["Nairobi", "Mombasa"],
        "min_value": Decimal(100),
        "max_value": Decimal(300),
    }
    return SeriesSpec(**(base | kw))  # type: ignore[arg-type]


def test_accepts_clean() -> None:
    r = check_observations([_o("Nairobi", "180.66"), _o("Mombasa", "179")], _spec(), [])
    assert r.status == "accepted" and r.failures == [] and r.checked == 2


def test_out_of_range_quarantines() -> None:
    r = check_observations([_o("Nairobi", "18066"), _o("Mombasa", "50")], _spec(), [])
    assert r.status == "quarantined" and len(r.failures) == 2


def test_unit_mismatch_quarantines() -> None:
    r = check_observations([_o("Nairobi", "180", unit="KES/kg"), _o("Mombasa", "179")], _spec(), [])
    assert r.status == "quarantined" and "unit" in r.failures[0]


def test_missing_entity_quarantines() -> None:
    r = check_observations([_o("Nairobi", "180")], _spec(), [])
    assert r.status == "quarantined" and "Mombasa" in r.failures[0]


def test_fewer_rows_quarantines() -> None:
    prev = [_stored(_o("Nairobi", "180", 8)), _stored(_o("Mombasa", "179", 8))]
    r = check_observations([_o("Nairobi", "181")], _spec(entities=[]), prev)
    assert r.status == "quarantined" and "fewer rows" in r.failures[0]


def test_totals_reconcile_and_fail() -> None:
    spec = _spec(entities=[], min_value=None, max_value=None, totals=[("Total", ["A", "B"])])
    ok = [_o("A", "10"), _o("B", "20.3"), _o("Total", "30")]
    assert check_observations(ok, spec, []).status == "accepted"
    bad = [_o("A", "10"), _o("B", "21"), _o("Total", "30")]
    r = check_observations(bad, spec, [])
    assert r.status == "quarantined" and "totals mismatch" in r.failures[0]
    partial = [_o("A", "10"), _o("Total", "30")]
    assert check_observations(partial, spec, []).status == "accepted"


def test_jump_is_warning() -> None:
    prev = [_stored(_o("Nairobi", "100", 8)), _stored(_o("Mombasa", "0", 8))]
    spec = _spec(max_step_pct=Decimal(10), min_value=None, max_value=None)
    r = check_observations([_o("Nairobi", "150"), _o("Mombasa", "5")], spec, prev)
    assert r.status == "accepted" and len(r.warnings) == 1
    assert "Nairobi" in r.warnings[0] and "2026-09" in r.warnings[0]
    first = check_observations([_o("Nairobi", "150"), _o("Mombasa", "5")], spec, [])
    assert first.warnings == []


def test_duplicate_conflict_quarantines() -> None:
    spec = _spec(entities=[])
    r = check_observations([_o("Nairobi", "180"), _o("Nairobi", "181")], spec, [])
    assert r.status == "quarantined" and "duplicate" in r.failures[0]
    same = check_observations([_o("Nairobi", "180"), _o("Nairobi", "180")], spec, [])
    assert same.status == "accepted"


def test_period_type_mismatch_quarantines() -> None:
    o = _o("Nairobi", "180").model_copy(
        update={"period": month(2026, 9).model_copy(update={"type": "week"})}
    )
    r = check_observations([o], _spec(entities=[]), [])
    assert r.status == "quarantined"


def test_empty_quarantines() -> None:
    assert check_observations([], _spec(entities=[]), []).status == "quarantined"


def test_same_entity_different_metrics_not_duplicate() -> None:
    a = _o("Nairobi", "180")
    b = a.model_copy(update={"metric": "diesel", "value": Decimal("170")})
    r = check_observations([a, b], _spec(entities=[]), [])
    assert not any("duplicate" in f for f in r.failures)
