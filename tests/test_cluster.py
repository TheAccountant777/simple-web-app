from datetime import UTC, datetime, timedelta

from conftest import function_model_returning

from kenya_data_engine.models import Signal
from kenya_data_engine.synth.cluster import (
    Cluster,
    ClusterOutput,
    cluster_signals,
    format_signals,
)

BASE = datetime(2026, 10, 9, tzinfo=UTC)


def sig(id, kind="news", days=0, title="T"):
    return Signal(
        id=id,
        kind=kind,
        title=title,
        source="src",
        url=None,
        published_at=BASE - timedelta(days=days),
    )


def test_format_signals():
    s = sig("a1", title="x" * 200)
    assert format_signals([s]) == f"a1 | news | src | 2026-10-09 | {'x' * 120}"
    assert format_signals([sig("b")]).startswith("b | news | src | 2026-10-09 |")


def test_format_signal_without_date():
    s = sig("a1").model_copy(update={"published_at": None})
    assert " |  | " in format_signals([s])


async def test_unknown_ids_and_empty_clusters_dropped(ctx):  # Review Focus 4
    fm = function_model_returning(
        ClusterOutput(
            clusters=[
                Cluster(
                    title="Fuel", summary="s", category="personal_finance", signal_ids=["a1", "zzz"]
                ),
                Cluster(title="Ghost", summary="s", category="economy", signal_ids=["nope"]),
            ]
        )
    )
    clusters, dropped = await cluster_signals([sig("a1"), sig("b2")], ctx, model=fm)
    assert [c.signal_ids for c in clusters] == [["a1"]]
    assert dropped == ["Ghost: no valid signals"]


async def test_signal_in_at_most_one_cluster(ctx):
    fm = function_model_returning(
        ClusterOutput(
            clusters=[
                Cluster(title="A", summary="s", category="economy", signal_ids=["a1", "a1"]),
                Cluster(title="B", summary="s", category="economy", signal_ids=["a1"]),
            ]
        )
    )
    clusters, dropped = await cluster_signals([sig("a1")], ctx, model=fm)
    assert [c.title for c in clusters] == ["A"] and clusters[0].signal_ids == ["a1"]
    assert dropped == ["B: no valid signals"]


async def test_empty_signals_makes_no_call(ctx):
    assert await cluster_signals([], ctx, model=function_model_returning(lambda p: 1 / 0)) == (
        [],
        [],
    )


async def test_truncation_keeps_calendar_signals(ctx):
    ctx.config.synth.max_signals = 2
    seen: list[str] = []

    def out(prompt):
        seen.append(prompt)
        return ClusterOutput(clusters=[])

    signals = [
        sig("n1", days=1),
        sig("n2", days=2),
        sig("n3", days=3),
        sig("c1", kind="calendar", days=-30),
    ]
    await cluster_signals(signals, ctx, model=function_model_returning(out))
    p = seen[0]
    assert "c1 |" in p and "n1 |" in p and "n2 |" not in p and "n3 |" not in p
