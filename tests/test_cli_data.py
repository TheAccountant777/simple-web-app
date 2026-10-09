import json
import os
import time
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from kenya_data_engine.cli.app import app
from kenya_data_engine.data.models import Observation, Provenance
from kenya_data_engine.data.periods import year
from kenya_data_engine.data.store import BlobStore, SeriesStore

pytestmark = pytest.mark.usefixtures("data_net")

WB = "https://api.worldbank.org/v2/country/KEN/indicator/FP.CPI.TOTL.ZG"
ROWS = [
    {"date": "2023", "value": 7.67, "indicator": {"value": "x"}},
    {"date": "2022", "value": 7.7, "indicator": {"value": "x"}},
]
ENV = {"COLUMNS": "200"}


@pytest.fixture
def cli(tmp_home, monkeypatch):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_home.root))
    return CliRunner()


def run(cli, *args):
    return cli.invoke(app, list(args), env=ENV)


def test_data_list_shows_catalog_and_rows(cli):
    r = run(cli, "data", "list")
    assert r.exit_code == 0 and "wb:FP.CPI.TOTL.ZG" in r.output and "imf:cpi" in r.output
    rows = json.loads(run(cli, "data", "list", "--json").output)
    assert {x["key"]: x["enabled"] for x in rows}["imf:cpi"] is False


def test_data_fetch_and_show_worldbank(cli, respx_mock):
    respx_mock.get(WB).respond(json=[{"page": 1}, ROWS])
    r = run(cli, "data", "fetch", "wb:FP.CPI.TOTL.ZG")
    assert r.exit_code == 0, r.output
    assert "new 2" in r.output and "accepted" in r.output
    s = run(cli, "data", "show", "wb:FP.CPI.TOTL.ZG")
    assert s.exit_code == 0
    assert "2023" in s.output and "7.67" in s.output and "api.worldbank.org" in s.output
    last = run(cli, "data", "show", "wb:FP.CPI.TOTL.ZG", "--last", "1", "--entity", "Kenya")
    assert "2023" in last.output and "2022" not in last.output
    j = run(cli, "data", "fetch", "wb:FP.CPI.TOTL.ZG", "--json")
    assert json.loads(j.output)["added"] == [0, 2, 0]


def test_data_fetch_error_and_disabled_exit_codes(cli, respx_mock):
    respx_mock.get(WB).respond(404)
    r = run(cli, "data", "fetch", "wb:FP.CPI.TOTL.ZG")
    assert r.exit_code == 1 and "404" in r.output
    d = run(cli, "data", "fetch", "imf:cpi")
    assert d.exit_code == 2 and "disabled" in d.output
    assert run(cli, "data", "show", "nope").exit_code == 2


def test_data_fetch_quarantine_exits_1(cli, respx_mock, tmp_home):
    tmp_home.catalog_path.write_text(
        "wb:FP.CPI.TOTL.ZG:\n"
        "  spec: {metric: inflation, unit: '%', period_type: year, max_value: 1}\n"
    )
    respx_mock.get(WB).respond(json=[{"page": 1}, ROWS])
    r = run(cli, "data", "fetch", "wb:FP.CPI.TOTL.ZG")
    assert r.exit_code == 1 and "quarantined" in r.output


def test_data_show_csv(cli, respx_mock):
    respx_mock.get(WB).respond(json=[{"page": 1}, ROWS])
    run(cli, "data", "fetch", "wb:FP.CPI.TOTL.ZG")
    r = run(cli, "data", "show", "wb:FP.CPI.TOTL.ZG", "--csv")
    lines = r.output.strip().splitlines()
    assert lines[0] == "period,entity,metric,value,unit,revised,source"
    assert lines[2] == "2023,Kenya,inflation,7.67,%,,api.worldbank.org"


def test_catalog_probe_reports_and_saves_samples(cli, respx_mock, tmp_home, tmp_path):
    respx_mock.get(WB).respond(json=[{"page": 1}, ROWS])
    out = tmp_path / "samples"
    r = run(cli, "catalog", "probe", "wb:FP.CPI.TOTL.ZG", "--save-samples", str(out), "--json")
    assert r.exit_code == 0, r.output
    (res,) = json.loads(r.output)
    assert res["status"] == "ok" and res["sniffed"] == "json" and res["links_found"] == 1
    assert res["final_url"].startswith(WB)
    saved = list((out / "wb_FP.CPI.TOTL.ZG").iterdir())
    assert len(saved) == 1 and json.loads(saved[0].read_bytes())[1] == ROWS
    assert json.loads((out / "probe.json").read_text())[0]["key"] == "wb:FP.CPI.TOTL.ZG"
    assert SeriesStore(tmp_home.db_path).series_keys() == []
    assert not list(tmp_home.runs_dir.iterdir())


def test_catalog_probe_listing_text_includes_disabled(cli, respx_mock):
    respx_mock.get("https://new.kenyalaw.org/gazettes/").respond(
        200, text='<a href="/akn/ke/officialGazette/2026-01-02/1/eng">G</a>'
    )
    respx_mock.get("https://new.kenyalaw.org/akn/ke/officialGazette/2026-01-02/1/eng").respond(
        200, text="<html>gazette</html>"
    )
    r = run(cli, "catalog", "probe", "kenyalaw.gazette")
    assert r.exit_code == 0 and "(disabled)" in r.output and "1 link(s)" in r.output
    assert "html" in r.output


def test_catalog_probe_handles_404(cli, respx_mock):
    respx_mock.get(WB).respond(404)
    r = run(cli, "catalog", "probe", "wb:FP.CPI.TOTL.ZG")
    assert r.exit_code == 0 and "404" in r.output
    assert (
        json.loads(run(cli, "catalog", "probe", "wb:FP.CPI.TOTL.ZG", "--json").output)[0]["status"]
        == "error"
    )


def test_catalog_probe_needs_keys_or_all(cli):
    assert run(cli, "catalog", "probe").exit_code == 2
    assert run(cli, "catalog", "probe", "nope").exit_code == 2


def test_gc_prunes_unreferenced(cli, tmp_home):
    blobs, store = BlobStore(tmp_home.blobs_dir), SeriesStore(tmp_home.db_path)
    kept, old, fresh = blobs.put(b"kept"), blobs.put(b"old-orphan"), blobs.put(b"fresh")
    long_ago = time.time() - 200 * 86400
    for sha in (kept, old):
        os.utime(blobs.path(sha), (long_ago, long_ago))
    store.add(
        [
            Observation(
                series="s",
                period=year(2020),
                entity="Kenya",
                metric="m",
                value=1,
                unit="u",
                provenance=Provenance(
                    url="https://e.org/x",
                    blob_sha256=kept,
                    retrieved_at=datetime.now(UTC),
                    locator="l",
                    extractor="e",
                ),
            )
        ]
    )
    declined = cli.invoke(app, ["gc"], input="n\n", env=ENV)
    assert declined.exit_code == 1 and blobs.path(old).exists()
    r = run(cli, "gc", "--yes")
    assert r.exit_code == 0 and "Pruned 1 blob(s), freed 10 bytes" in r.output
    assert not blobs.path(old).exists() and blobs.path(kept).exists() and blobs.path(fresh).exists()
    assert "Nothing to prune" in run(cli, "gc", "--yes").output
    assert run(cli, "gc", "--older-than", "soon").exit_code == 2


def test_show_survives_markup_in_scraped_strings(cli, tmp_home):
    SeriesStore(tmp_home.db_path).add(
        [
            Observation(
                series="wb:FP.CPI.TOTL.ZG",
                period=year(2020),
                entity="[/x]",
                metric="inflation",
                value=1,
                unit="%",
                provenance=Provenance(
                    url="https://e.org/x",
                    blob_sha256="a",
                    retrieved_at=datetime.now(UTC),
                    locator="l",
                    extractor="e",
                ),
            )
        ]
    )
    r = run(cli, "data", "show", "wb:FP.CPI.TOTL.ZG")
    assert r.exit_code == 0 and "[/x]" in r.output
