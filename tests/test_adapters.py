import io
import json
from decimal import Decimal

import httpx
import openpyxl
import pytest

from kenya_data_engine.data.adapters import ADAPTERS
from kenya_data_engine.data.adapters.base import Discovered
from kenya_data_engine.data.registry import CatalogEntry, fetch_series
from kenya_data_engine.data.store import AddResult, BlobStore, SeriesStore
from kenya_data_engine.errors import ExtractError

pytestmark = pytest.mark.usefixtures("data_net")

WB_URL = "https://api.worldbank.org/v2/country/KEN/indicator/FP.CPI.TOTL.ZG"
WB_ROWS = [
    {"date": "2023", "value": 7.67, "indicator": {"value": "Inflation"}},
    {"date": "2022", "value": None, "indicator": {"value": "Inflation"}},
    {"date": "2021", "value": 6.11, "indicator": {"value": "Inflation"}},
]


def _wb(respx_mock, rows=None):
    return respx_mock.get(WB_URL).respond(json=[{"page": 1}, rows or WB_ROWS])


async def test_worldbank_fetch_series(ctx, respx_mock):
    route = _wb(respx_mock)
    out = await fetch_series("wb:FP.CPI.TOTL.ZG", ctx)
    assert route.called
    assert out.error is None and out.fetched == 1
    assert out.added == AddResult(new=2, unchanged=0, revised=0)
    assert out.report and out.report.status == "accepted"
    rows = SeriesStore(ctx.home.db_path).latest("wb:FP.CPI.TOTL.ZG")
    assert [r.period.label for r in rows] == ["2021", "2023"]
    assert rows[1].value == Decimal("7.67") and rows[1].unit == "%"
    assert rows[1].provenance.locator == "api:/v2/country/KEN/indicator/FP.CPI.TOTL.ZG#2023"
    again = await fetch_series("wb:FP.CPI.TOTL.ZG", ctx)
    assert again.added == AddResult(new=0, unchanged=2, revised=0)


SDMX = {
    "data": {
        "dataSets": [
            {"series": {"0:0": {"observations": {"0": ["101.5"], "1": [102.25], "2": [None]}}}}
        ],
        "structure": {
            "dimensions": {
                "observation": [
                    {"values": [{"id": "2025-01"}, {"id": "2025-02"}, {"id": "2025-03"}]}
                ]
            }
        },
    }
}


def _sdmx_entry() -> CatalogEntry:
    return CatalogEntry.model_validate(
        {
            "key": "imf:t",
            "adapter": "sdmx",
            "title": "T",
            "publisher": "IMF",
            "tier": 2,
            "spec": {"key": "imf:t", "metric": "cpi", "unit": "index", "period_type": "month"},
            "params": {"base_url": "https://sdmx.example.org/rest/", "flow": "CPI", "key": "KEN.M"},
        }
    )


async def test_sdmx_parse(ctx):
    entry, adapter = _sdmx_entry(), ADAPTERS["sdmx"]
    (item,) = await adapter.discover(entry, ctx)
    assert item.url == "https://sdmx.example.org/rest/data/CPI/KEN.M?startPeriod=2000"
    obs = await adapter.observations(entry, item, json.dumps(SDMX).encode(), "sha", ctx)
    assert [(o.period.label, o.value) for o in obs] == [
        ("2025-01", Decimal("101.5")),
        ("2025-02", Decimal("102.25")),
    ]
    assert obs[0].provenance.locator == "sdmx:0:0/2025-01"


LISTING = """<html><body>
<a href="/files/CPI Bulletin January 2026.xlsx">CPI Bulletin January 2026</a>
<a href="/files/CPI-2026-02.xlsx">February release</a>
<a href="/about">About us</a>
<a href="/files/CPI-2026-02.xlsx">dup</a>
</body></html>"""


def _listing_entry(**over) -> CatalogEntry:
    data = {
        "key": "x.cpi",
        "adapter": "listing",
        "title": "T",
        "publisher": "P",
        "tier": 1,
        "spec": {
            "key": "x.cpi",
            "metric": "price",
            "metrics": ["super", "diesel"],
            "unit": "KES",
            "period_type": "month",
        },
        "params": {
            "url": "https://example.org/data/",
            "link_pattern": r"/files/.*\.xlsx$",
            "date_pattern": r"(\d{4}-\d{2}|[A-Za-z]+ \d{4})",
            "columns": {"Town": "entity", "Super": "value:super", "Diesel": "value:diesel"},
        },
    } | over
    return CatalogEntry.model_validate(data)


def _xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Town", "Super", "Diesel", "Notes"])
    ws.append(["Nairobi", 180.66, "170.5", "x"])
    ws.append(["Mombasa", "179.1", "-", "x"])
    ws.append(["Kisumu", 181.0, 171.0, "x"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_listing_discover_pattern_and_dates(ctx, respx_mock):
    respx_mock.get("https://example.org/data/").respond(200, text=LISTING)
    items = await ADAPTERS["listing"].discover(_listing_entry(), ctx)
    assert [(i.url, str(i.published)) for i in items] == [
        ("https://example.org/files/CPI%20Bulletin%20January%202026.xlsx", "2026-01-01"),
        ("https://example.org/files/CPI-2026-02.xlsx", "2026-02-01"),
    ]
    assert items[0].title == "CPI Bulletin January 2026"


async def test_listing_series_extract_maps_columns(ctx, respx_mock, tmp_home):
    respx_mock.get("https://example.org/data/").respond(200, text=LISTING)
    respx_mock.get("https://example.org/files/CPI-2026-02.xlsx").respond(200, content=_xlsx())
    respx_mock.get("https://example.org/files/CPI%20Bulletin%20January%202026.xlsx").respond(404)
    tmp_home.catalog_path.write_text(
        "x.cpi:\n  adapter: listing\n  title: T\n  publisher: P\n  tier: 1\n"
        "  spec: {metric: price, metrics: [super, diesel], unit: KES, period_type: month}\n"
        "  params:\n    url: https://example.org/data/\n    link_pattern: '/files/.*\\.xlsx$'\n"
        "    date_pattern: '(\\d{4}-\\d{2}|[A-Za-z]+ \\d{4})'\n"
        "    columns: {Town: entity, Super: 'value:super', Diesel: 'value:diesel'}\n"
    )
    out = await fetch_series("x.cpi", ctx)
    assert out.discovered == 2 and out.fetched == 1
    assert out.error and "404" in out.error  # soft failure on one item, the rest still ran
    assert out.added == AddResult(new=5, unchanged=0, revised=0)  # Mombasa Diesel "-" skipped
    rows = SeriesStore(ctx.home.db_path).latest("x.cpi")
    got = {(r.entity, r.metric): r.value for r in rows}
    assert got[("Nairobi", "super")] == Decimal("180.66")
    assert got[("Nairobi", "diesel")] == Decimal("170.5")
    assert all(r.period.label == "2026-02" for r in rows)
    assert rows[0].provenance.locator.startswith("Sheet")


async def test_quarantine_adds_nothing_keeps_blob(ctx, respx_mock, tmp_home):
    _wb(respx_mock, [{"date": "2023", "value": 9999, "indicator": {"value": "I"}}])
    tmp_home.catalog_path.write_text(
        "wb:FP.CPI.TOTL.ZG:\n"
        "  spec: {metric: inflation, unit: '%', period_type: year, max_value: 100}\n"
    )
    out = await fetch_series("wb:FP.CPI.TOTL.ZG", ctx)
    assert out.report and out.report.status == "quarantined"
    assert out.report.failures and out.added == AddResult(0, 0, 0)
    assert SeriesStore(ctx.home.db_path).latest("wb:FP.CPI.TOTL.ZG") == []
    blob = BlobStore(ctx.home.blobs_dir)
    assert len(list(blob.root.glob("*/*"))) == 1


async def test_document_only_entry_only_discovers(ctx, respx_mock, tmp_home):
    respx_mock.get("https://new.kenyalaw.org/gazettes/").respond(
        200, text='<a href="/akn/ke/officialGazette/2026-01-02/1/eng">Gazette 2026-01-02</a>'
    )
    tmp_home.catalog_path.write_text("kenyalaw.gazette:\n  enabled: true\n")
    out = await fetch_series("kenyalaw.gazette", ctx)
    assert (out.discovered, out.fetched, out.added, out.error) == (1, 0, None, None)


async def test_discovery_failure_is_soft(ctx, respx_mock):
    respx_mock.get(WB_URL).mock(return_value=httpx.Response(404))
    out = await fetch_series("wb:FP.CPI.TOTL.ZG", ctx)
    assert out.fetched == 0 and out.error and "404" in out.error
    assert Discovered(url="u").published is None


async def test_sdmx_multi_series_entities_distinct(ctx):
    doc = {
        "data": {
            "dataSets": [
                {
                    "series": {
                        "0": {"observations": {"0": [1]}},
                        "1": {"observations": {"0": [2]}},
                    }
                }
            ],
            "structure": {
                "dimensions": {
                    "series": [{"values": [{"id": "KEN"}, {"id": "UGA"}]}],
                    "observation": [{"values": [{"id": "2025-01"}]}],
                }
            },
        }
    }
    entry = _sdmx_entry()
    (item,) = await ADAPTERS["sdmx"].discover(entry, ctx)
    obs = await ADAPTERS["sdmx"].observations(entry, item, json.dumps(doc).encode(), "s", ctx)
    assert sorted((o.entity, o.value) for o in obs) == [("KEN", 1), ("UGA", 2)]


async def test_worldbank_error_and_pages(ctx):
    entry = (await _catalog(ctx))["wb:FP.CPI.TOTL.ZG"]
    wb = ADAPTERS["worldbank"]
    item = Discovered(url="u")
    err = json.dumps([{"message": [{"id": "120", "value": "Invalid"}]}]).encode()
    with pytest.raises(ExtractError, match="World Bank error"):
        await wb.observations(entry, item, err, "s", ctx)
    paged = json.dumps([{"pages": 2}, []]).encode()
    with pytest.raises(ExtractError, match="truncated"):
        await wb.observations(entry, item, paged, "s", ctx)


async def _catalog(ctx):
    from kenya_data_engine.data.registry import load_catalog

    return load_catalog(ctx.home)


async def _listing_run(ctx, respx_mock, tmp_home, body: str, unit: str = "KES"):
    respx_mock.get("https://example.org/data/").respond(
        200, text='<a href="/files/CPI-2026-02.html">Feb 2026</a>'
    )
    respx_mock.get("https://example.org/files/CPI-2026-02.html").respond(200, text=body)
    tmp_home.catalog_path.write_text(
        "x.cpi:\n  adapter: listing\n  title: T\n  publisher: P\n  tier: 1\n"
        f"  spec: {{metric: price, metrics: [super, diesel], unit: {unit}, period_type: month}}\n"
        "  params:\n    url: https://example.org/data/\n    link_pattern: '/files/.*\\.html$'\n"
        "    date_pattern: '(\\d{4}-\\d{2})'\n"
        "    columns: {Town: entity, Super: 'value:super', Diesel: 'value:diesel'}\n"
    )
    return await fetch_series("x.cpi", ctx)


def _table(*rows: str) -> str:
    head = "<tr><th>Town</th><th>Super</th><th>Diesel</th></tr>"
    ok = "<tr><td>Kisumu</td><td>181</td><td>171</td></tr>"  # keeps header detection honest
    return f"<table>{head}{ok}{''.join(rows)}</table>"


async def test_spanned_cell_quarantines_and_stores_no_diesel(ctx, respx_mock, tmp_home):
    body = _table(
        "<tr><td>Nairobi</td><td colspan=2>180.50</td></tr>",
        "<tr><td>Mombasa</td><td>179</td><td>169</td></tr>",
    )
    out = await _listing_run(ctx, respx_mock, tmp_home, body)
    assert out.report and out.report.status == "quarantined"
    assert any("spanned" in f for f in out.report.failures)
    assert SeriesStore(ctx.home.db_path).latest("x.cpi") == []


async def test_cell_unit_pct_in_kes_series_quarantines(ctx, respx_mock, tmp_home):
    body = _table("<tr><td>Nairobi</td><td>180</td><td>45%</td></tr>")
    out = await _listing_run(ctx, respx_mock, tmp_home, body)
    assert out.report and out.report.status == "quarantined"
    assert any("unit mismatch" in f for f in out.report.failures)


async def test_na_cell_is_missing_not_a_failure(ctx, respx_mock, tmp_home):
    body = _table("<tr><td>Nairobi</td><td>180</td><td>n/a</td></tr>")
    out = await _listing_run(ctx, respx_mock, tmp_home, body)
    assert out.report and out.report.status == "accepted"
    assert out.added == AddResult(new=3, unchanged=0, revised=0)  # no Nairobi diesel


async def test_unparsable_cell_quarantines(ctx, respx_mock, tmp_home):
    body = _table("<tr><td>Nairobi</td><td>180</td><td>USD 5</td></tr>")
    out = await _listing_run(ctx, respx_mock, tmp_home, body)
    assert out.report and out.report.status == "quarantined"
    assert any("unparsable value 'USD 5'" in f for f in out.report.failures)
    assert SeriesStore(ctx.home.db_path).latest("x.cpi") == []


async def test_compound_series_unit_survives_currency_cells(ctx, respx_mock, tmp_home):
    body = _table("<tr><td>Nairobi</td><td>Sh180.5</td><td>170</td></tr>")
    out = await _listing_run(ctx, respx_mock, tmp_home, body, unit="KES/L")
    assert out.report and out.report.status == "accepted"


async def test_provenance_url_is_final_url_and_time_is_fetch_time(ctx, respx_mock):
    moved = "https://api.worldbank.org/moved/v2"
    respx_mock.get(WB_URL).respond(302, headers={"location": moved})
    respx_mock.get(moved).respond(json=[{"page": 1}, WB_ROWS])
    await fetch_series("wb:FP.CPI.TOTL.ZG", ctx)
    first = SeriesStore(ctx.home.db_path).latest("wb:FP.CPI.TOTL.ZG")[0].provenance
    assert first.url == moved
    await fetch_series("wb:FP.CPI.TOTL.ZG", ctx)  # cache hit: same final URL, original time
    again = SeriesStore(ctx.home.db_path).latest("wb:FP.CPI.TOTL.ZG")[0].provenance
    assert again.url == first.url and again.retrieved_at == first.retrieved_at
