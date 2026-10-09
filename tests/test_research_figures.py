import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal

from kenya_data_engine.data.models import Observation, Provenance
from kenya_data_engine.data.periods import month
from kenya_data_engine.data.store import SeriesStore
from kenya_data_engine.research.figures import CSV_HEADER, figures_for
from kenya_data_engine.research.models import DataNeed, NeedStatus

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def _obs(series, entity, y, m, value, unit="KES/L", published=None):
    return Observation(
        series=series, period=month(y, m), entity=entity, metric="super", value=Decimal(value),
        unit=unit, provenance=Provenance(
            url="https://epra.go.ke/p", blob_sha256="a" * 64, retrieved_at=NOW,
            published=published, locator="l", extractor="e",
        ),
    )  # fmt: skip


def _need(i="n1", **kw):
    base = dict(id=i, kind="series", question="q", metric="super", entities=["Nairobi"])
    return DataNeed(**{**base, **kw})


def _status(i="n1", status="satisfied", keys=("pump",)):
    return NeedStatus(need_id=i, status=status, series_keys=list(keys))


def _store(tmp_path, rows):
    store = SeriesStore(tmp_path / "s.db")
    store.add(rows)
    return store


def test_latest_change_pct_yoy_for_monthly_series(tmp_path):
    store = _store(
        tmp_path,
        [
            _obs("pump", "Nairobi", 2025, 9, "160"),
            _obs("pump", "Nairobi", 2026, 8, "180"),
            _obs("pump", "Nairobi", 2026, 9, "198", published=date(2026, 9, 15)),
        ],
    )
    st = _status()
    fbook, pack = figures_for([_need()], [st], store, {"pump": 1}, set())
    vals = {f.id: (f.value, f.unit, f.formula) for f in fbook.figures}
    assert vals["F1"] == (Decimal("198"), "KES/L", "value")
    assert vals["F2"][0] == Decimal("18") and vals["F2"][1] == "KES/L"
    assert vals["F3"][0] == Decimal("10") and vals["F3"][1] == "pct"
    assert vals["F4"][0] == Decimal("23.75") and vals["F4"][1] == "pct"  # yoy vs 2025-09
    assert len(fbook.figures) == 4
    assert [r.label for r in pack.refs] == ["F1", "F2", "F3", "F4"]
    assert pack.refs[0].tier == 1 and pack.refs[0].published == date(2026, 9, 15)
    assert pack.refs[0].period_label == "2026-09" and st.figure_ids == ["F1", "F2", "F3", "F4"]
    assert "| F1 |" in pack.markdown


def test_single_observation_has_only_latest(tmp_path):
    store = _store(tmp_path, [_obs("pump", "Nairobi", 2026, 9, "198")])
    fbook, _ = figures_for([_need()], [_status()], store, {}, set())
    assert [f.id for f in fbook.figures] == ["F1"]


def test_generic_flag_propagates(tmp_path):
    store = _store(
        tmp_path, [_obs("pump", "Nairobi", 2026, 8, "1"), _obs("gen", "Nairobi", 2026, 8, "2")]
    )
    _, pack = figures_for(
        [_need(), _need("n2")],
        [_status(), _status("n2", keys=("gen",))],
        store,
        {"pump": 1, "gen": 3},
        {"gen"},
    )
    assert [(r.series, r.generic, r.tier) for r in pack.refs] == [
        ("pump", False, 1),
        ("gen", True, 3),
    ]


def test_not_found_and_fact_needs_skipped(tmp_path):
    store = _store(tmp_path, [_obs("pump", "Nairobi", 2026, 8, "1")])
    needs = [_need(), _need("n2", kind="fact")]
    statuses = [_status(status="not_found"), _status("n2")]
    fbook, pack = figures_for(needs, statuses, store, {}, set())
    assert fbook.figures == [] and pack.refs == []


def test_entity_cap_5(tmp_path):
    names = [f"Town{i}" for i in range(8)]
    store = _store(tmp_path, [_obs("pump", n, 2026, 9, "1") for n in names])
    need = _need(entities=names)
    _, pack = figures_for([need], [_status()], store, {}, set())
    assert [r.entity for r in pack.refs] == names[:5]  # need order, first five
    _, pack = figures_for([_need(entities=[])], [_status()], store, {}, set())
    assert len(pack.refs) == 5


def test_comparisons_csv_header_and_rows(tmp_path):
    store = _store(
        tmp_path,
        [_obs("pump", "Nairobi", 2026, 8, "180"), _obs("pump", "Nairobi", 2026, 9, "198")],
    )
    _, pack = figures_for([_need()], [_status()], store, {}, set())
    rows = list(csv.reader(io.StringIO(pack.comparisons_csv)))
    assert rows[0] == CSV_HEADER == [
        "series", "entity", "metric", "period", "value", "unit", "source_url", "vintage",
    ]  # fmt: skip
    assert rows[1:] == [
        ["pump", "Nairobi", "super", "2026-08", "180", "KES/L", "https://epra.go.ke/p", "1"],
        ["pump", "Nairobi", "super", "2026-09", "198", "KES/L", "https://epra.go.ke/p", "1"],
    ]


def test_kinds_and_case_insensitive_entities(tmp_path):
    store = _store(
        tmp_path,
        [_obs("pump", "nairobi", 2026, 8, "180"), _obs("pump", "Nairobi", 2026, 9, "198")],
    )
    _, pack = figures_for([_need()], [_status()], store, {}, set())
    assert [(r.kind, r.metric) for r in pack.refs] == [
        ("latest", "super"), ("change", "super"), ("pct_change", "super"),
    ]  # fmt: skip
