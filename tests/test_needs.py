import io
from datetime import UTC, date, datetime
from decimal import Decimal

import openpyxl
import pytest
from research_util import make_deps

from kenya_data_engine.data.extract.grid import Locator
from kenya_data_engine.data.models import Observation, Provenance
from kenya_data_engine.data.periods import month, year
from kenya_data_engine.data.store import SeriesStore
from kenya_data_engine.research.models import DataNeed, DataSourceSpec
from kenya_data_engine.research.needs import ExecResult, execute, need_status

NOW = datetime(2026, 10, 9, tzinfo=UTC)
XLSX_URL = "https://www.knbs.or.ke/fuel.xlsx"
WB_URL = "https://api.worldbank.org/v2/country/KEN/indicator/FP.CPI.TOTL.ZG"


def _need(**kw):
    base = dict(
        id="n1", kind="series", question="q", metric="super petrol", unit="KES/L",
        frequency="monthly", entities=["Nairobi", "Mombasa"], min_points=2,
    )  # fmt: skip
    return DataNeed(**{**base, **kw})


def _xlsx(rows=None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Town", "Month", "Super", "Notes"])
    for row in rows or [
        ["NAIROBI", "2026-08", 180.5, "x"],
        ["Mombasa", "2026-08", "179.1", "x"],
        ["Mombasa", "2026-09", "-", "x"],
        ["Nairobi", "2026-09", 182, "x"],
    ]:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _file_spec(url=XLSX_URL, **kw):
    cols = {"Town": "entity", "Month": "period", "Super": "value:super_petrol"}
    loc = Locator(columns=cols, header_rows=1)
    return DataSourceSpec(
        need="n1", via="file", url=url, locator=loc, publisher="knbs.or.ke", why="w", **kw
    )


def _allow(deps, *urls):
    deps.book.seen_urls.update(urls)


async def test_execute_registry_spec(ctx, respx_mock, tmp_path):
    respx_mock.get(WB_URL).respond(
        json=[
            {"page": 1},
            [
                {"date": "2023", "value": 7.67, "indicator": {"value": "Inflation"}},
                {"date": "2021", "value": 6.11, "indicator": {"value": "Inflation"}},
            ],
        ]
    )
    deps = make_deps(ctx, tmp_path)
    spec = DataSourceSpec(
        need="n1", via="registry", registry_key="wb:FP.CPI.TOTL.ZG", publisher="wb", why="w"
    )
    need = DataNeed(id="n1", kind="series", question="q", min_points=2)
    res = await execute(spec, need, deps)
    assert res.error is None and not res.generic
    assert res.series_keys == ["wb:FP.CPI.TOTL.ZG"] and res.points == 2
    assert res.report and res.report.status == "accepted"
    bad = spec.model_copy(update={"registry_key": "imf:cpi"})
    assert "disabled" in (await execute(bad, need, deps)).error


async def test_execute_generic_xlsx_columns_map(ctx, respx_mock, tmp_path):
    respx_mock.get("https://www.knbs.or.ke/robots.txt").respond(404)
    respx_mock.get(XLSX_URL).respond(200, content=_xlsx())
    deps = make_deps(ctx, tmp_path)
    _allow(deps, XLSX_URL)
    res = await execute(_file_spec(), _need(), deps)
    assert res.error is None and res.generic and res.report.status == "accepted"
    assert res.points == 3 and len(res.series_keys) == 1  # the "-" cell is simply missing
    key = res.series_keys[0]
    assert key.startswith("disc:www.knbs.or.ke:") and len(key.split(":")[2]) == 8
    rows = SeriesStore(ctx.home.db_path).latest(key)
    assert {(r.entity, r.period.label, r.value, r.unit, r.metric) for r in rows} == {
        ("Nairobi", "2026-08", Decimal("180.5"), "KES/L", "super_petrol"),  # NAIROBI -> Nairobi
        ("Mombasa", "2026-08", Decimal("179.1"), "KES/L", "super_petrol"),
        ("Nairobi", "2026-09", Decimal("182"), "KES/L", "super_petrol"),
    }
    assert rows[0].provenance.locator.startswith("Sheet!")
    again = await execute(_file_spec(), _need(), deps)  # same source, same series key
    assert again.series_keys == res.series_keys


async def test_execute_generic_needs_a_column_map_and_a_known_url(ctx, respx_mock, tmp_path):
    respx_mock.get("https://www.knbs.or.ke/robots.txt").respond(404)
    respx_mock.get(XLSX_URL).respond(200, content=_xlsx())
    deps = make_deps(ctx, tmp_path)
    unseen = await execute(_file_spec(), _need(), deps)
    assert "unknown url" in unseen.error
    _allow(deps, XLSX_URL)
    spec = _file_spec().model_copy(update={"locator": Locator(columns={"Nope": "entity"})})
    assert "none of the mapped columns" in (await execute(spec, _need(), deps)).error
    spec = spec.model_copy(update={"locator": None})
    assert "locator.columns is required" in (await execute(spec, _need(), deps)).error


async def test_execute_quarantine_reports(ctx, respx_mock, tmp_path):
    respx_mock.get("https://www.knbs.or.ke/robots.txt").respond(404)
    respx_mock.get(XLSX_URL).respond(200, content=_xlsx())
    deps = make_deps(ctx, tmp_path)
    _allow(deps, XLSX_URL)
    need = _need(entities=["Nairobi", "Kisumu"])  # Kisumu is not in the table
    res = await execute(_file_spec(), need, deps)
    assert res.error is None and res.series_keys == [] and res.points == 0
    assert res.report.status == "quarantined"
    assert any("missing expected entities" in f for f in res.report.failures)
    store = SeriesStore(ctx.home.db_path)
    st = need_status(need, [res], store, deps.book, 1)
    assert st.status == "not_found" and any("quarantined" in n for n in st.notes)


async def test_execute_page_text_evidence(ctx, respx_mock, tmp_path):
    url = "https://www.parliament.go.ke/bill"
    body = "The Finance Bill was read a second time in the National Assembly. " * 8
    respx_mock.get("https://www.parliament.go.ke/robots.txt").respond(404)
    respx_mock.get(url).respond(
        200, html=f"<html><body><article><p>{body}</p></article></body></html>"
    )
    deps = make_deps(ctx, tmp_path)
    spec = DataSourceSpec(need="n2", via="page_text", url=url, publisher="p", why="w")
    need = DataNeed(id="n2", kind="fact", question="stage?")
    assert "unknown url" in (await execute(spec, need, deps)).error
    _allow(deps, url)
    res = await execute(spec, need, deps)
    assert res.error is None and res.evidence_ids == ["ev1"] and not res.generic
    assert deps.book.items[0].tier == 1
    # robots.txt disallow is honoured
    respx_mock.get("https://www.parliament.go.ke/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /\n"
    )
    ctx.robots.clear()
    assert (await execute(spec, need, deps)).error == "disallowed by robots.txt"


def _obs(series, entity, period, value="1"):
    return Observation(
        series=series, period=period, entity=entity, metric="m", value=Decimal(value), unit="u",
        provenance=Provenance(
            url="https://x.ke/", blob_sha256="a" * 64, retrieved_at=NOW, locator="l", extractor="e"
        ),
    )  # fmt: skip


def test_need_status_series_points_and_period(ctx, tmp_path):
    store = SeriesStore(tmp_path / "s.db")
    store.add(
        [_obs("s1", e, month(2026, m)) for e in ("Nairobi", "Mombasa") for m in (6, 7, 8, 9)]
        + [_obs("s1", "Kisumu", month(2026, 9)), _obs("s1", "Nairobi", year(2020))]
    )
    deps = make_deps(ctx, tmp_path)
    res = [ExecResult(spec=DataSourceSpec(need="n1", via="file", publisher="p", why="w", url="u"),
                      series_keys=["s1"])]  # fmt: skip

    def st(**kw):
        return need_status(_need(**kw), res, store, deps.book, 2)

    s = st(period_start=date(2026, 8, 1), period_end=date(2026, 9, 30), min_points=4)
    assert (s.status, s.points, s.round, s.series_keys) == ("satisfied", 4, 2, ["s1"])
    assert st(period_start=date(2026, 8, 1), min_points=5).status == "partial"
    assert st(period_start=date(2026, 8, 1), period_end=date(2026, 8, 31), min_points=3).points == 2
    assert st(period_start=date(2027, 1, 1)).status == "not_found"
    assert st(entities=[], min_points=1).points == 10  # any entity
    assert st(entities=["nairobi"], period_start=date(2020, 1, 1), min_points=1).points == 5
    assert st(entities=["Nakuru"]).status == "not_found"
    errored = [ExecResult(spec=res[0].spec, error="boom")]
    s = need_status(_need(), errored, store, deps.book, 1)
    assert s.status == "not_found" and s.notes == ["u: boom"]


@pytest.mark.parametrize(
    ("urls", "expected"),
    [
        (["https://www.epra.go.ke/a"], "satisfied"),
        (["https://www.nation.africa/a", "https://www.cob.go.ke/b"], "satisfied"),
        (["https://www.nation.africa/a", "https://blog.example.com/b"], "partial"),
        ([], "not_found"),
    ],
)
def test_need_status_fact_by_tier(ctx, tmp_path, urls, expected):
    deps = make_deps(ctx, tmp_path)
    ids = [deps.book.add_text(u, "text", None, None).id for u in urls]
    spec = DataSourceSpec(need="n3", via="page_text", url="u", publisher="p", why="w")
    results = [ExecResult(spec=spec, evidence_ids=ids)] if ids else []
    need = DataNeed(id="n3", kind="fact", question="stage?")
    s = need_status(need, results, SeriesStore(tmp_path / "s.db"), deps.book, 1)
    assert s.status == expected and s.evidence_ids == ids
