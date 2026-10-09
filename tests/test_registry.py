import pytest

from kenya_data_engine.data.registry import fetch_series, load_catalog
from kenya_data_engine.errors import ConfigError, EngineError

pytestmark = pytest.mark.usefixtures("data_net")


def test_catalog_loads_and_merges_by_key(tmp_home):
    base = load_catalog(tmp_home)
    assert base["wb:FP.CPI.TOTL.ZG"].enabled and base["wb:PA.NUS.FCRF"].enabled
    assert base["wb:FP.CPI.TOTL.ZG"].spec.key == "wb:FP.CPI.TOTL.ZG"
    assert not base["imf:cpi"].enabled and "probe" in base["imf:cpi"].note
    assert base["kenyalaw.gazette"].spec is None
    assert base["epra.pump_prices"].spec.period_type == "epra_cycle"
    tmp_home.catalog_path.write_text(
        "epra.pump_prices:\n  enabled: true\n  note: verified\n"
        "mine.series:\n  adapter: worldbank\n  title: Mine\n  publisher: Me\n  tier: 3\n"
        "  params: {indicator: X.Y}\n"
    )
    merged = load_catalog(tmp_home)
    assert merged["epra.pump_prices"].enabled and merged["epra.pump_prices"].note == "verified"
    assert merged["epra.pump_prices"].params == base["epra.pump_prices"].params  # untouched
    assert merged["mine.series"].key == "mine.series"


def test_unknown_adapter_rejected(tmp_home):
    tmp_home.catalog_path.write_text(
        "bad:\n  adapter: scraper\n  title: T\n  publisher: P\n  tier: 1\n"
    )
    with pytest.raises(ConfigError, match="bad"):
        load_catalog(tmp_home)


async def test_disabled_entry_refused(ctx):
    with pytest.raises(EngineError, match="unverified; parser pending real samples"):
        await fetch_series("cbk.weekly_bulletin", ctx)
    with pytest.raises(EngineError, match="unknown series"):
        await fetch_series("nope", ctx)
