from datetime import UTC, datetime, timedelta

from kenya_data_engine.models import RadarResult, Signal, signal_id
from kenya_data_engine.pipeline import run_pipeline
from kenya_data_engine.radar.base import RadarStage, build_adapters, dedupe, run_radar

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def sig(title, url=None, published=None, source="s"):
    return Signal(
        id=signal_id(url, title),
        kind="news",
        title=title,
        source=source,
        url=url,
        published_at=published,
    )


class OkAdapter:
    name = "ok"

    def __init__(self, n):
        self.n = n

    async def fetch(self, ctx, since):
        return [sig(f"t{i}", f"https://a.ke/{i}", T0 + timedelta(hours=i)) for i in range(self.n)]


class BoomAdapter:
    name = "boom"

    async def fetch(self, ctx, since):
        raise RuntimeError("kaput")


async def test_failed_adapter_does_not_stop_others(ctx):  # Review Focus 2
    res = await run_radar([OkAdapter(n=3), BoomAdapter()], ctx, since=T0)
    assert len(res.signals) == 3 and res.errors[0].adapter == "boom"
    assert res.errors[0].message == "kaput"
    assert res.collected_at.tzinfo is not None


async def test_results_sorted_newest_first_undated_last(ctx):
    class Mixed:
        name = "mixed"

        async def fetch(self, ctx, since):
            return [
                sig("undated", "https://a.ke/u"),
                sig("old", "https://a.ke/o", T0),
                sig("new", "https://a.ke/n", T0 + timedelta(days=1)),
            ]

    res = await run_radar([Mixed()], ctx, since=T0)
    assert [s.title for s in res.signals] == ["new", "old", "undated"]


def test_dedupe_by_url_and_title():
    same_url = [sig("A", "https://a.ke/x?utm_source=tw"), sig("B", "https://A.ke/x/")]
    assert [s.title for s in dedupe(same_url)] == ["A"]
    same_title = [sig("Rates  Up", "https://a.ke/1"), sig("rates up", "https://b.ke/2")]
    assert len(dedupe(same_title)) == 1
    no_url = [sig("Budget Day"), sig("budget  day"), sig("Other")]
    assert [s.title for s in dedupe(no_url)] == ["Budget Day", "Other"]


async def test_radar_stage_sets_since_from_config(ctx, monkeypatch):
    seen = {}

    class Spy:
        name = "spy"

        async def fetch(self, ctx, since):
            seen["since"] = since
            return []

    monkeypatch.setattr("kenya_data_engine.radar.base.build_adapters", lambda c, h: [Spy()])
    stage = RadarStage()
    assert (stage.name, stage.output_name, stage.output_type) == ("radar", "signals", RadarResult)
    out = await run_pipeline([stage], ctx)
    assert isinstance(out, RadarResult)
    age = datetime.now(UTC) - seen["since"]
    assert abs(age - timedelta(hours=ctx.config.radar.since_hours)) < timedelta(minutes=1)


def test_build_adapters_respects_enabled(ctx):
    names = {a.name for a in build_adapters(ctx.config, ctx.home)}
    assert {"nation", "business_daily", "google_trends", "calendar"} <= names
    cfg = ctx.config.model_copy(deep=True)
    cfg.radar.enabled = ["trends"]
    assert [a.name for a in build_adapters(cfg, ctx.home)] == ["google_trends"]
    cfg.radar.enabled = []
    assert build_adapters(cfg, ctx.home) == []
