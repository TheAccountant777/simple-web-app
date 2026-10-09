from datetime import UTC, datetime

import pytest
from conftest import function_model_returning

from kenya_data_engine.errors import BudgetExceeded
from kenya_data_engine.models import RadarResult, Signal, TopicList
from kenya_data_engine.synth.cluster import Cluster, ClusterOutput
from kenya_data_engine.synth.stage import SynthesizeStage


def radar(n):
    sigs = [
        Signal(id=f"s{i}", kind="news", title=f"T{i}", source="x", url=None, published_at=None)
        for i in range(n)
    ]
    return RadarResult(signals=sigs, errors=[], collected_at=datetime(2026, 10, 9, tzinfo=UTC))


def score_dict(da=3, **kw):
    d = dict(
        data_ability=da,
        wallet_impact=3,
        timeliness=3,
        clarity_gap=3,
        novelty=3,
        justification={},
        why_now="now",
    )
    return {**d, **kw}


def stage_model(n, scorer):
    """One model that answers both the cluster call and per-cluster score calls."""

    def out(prompt):
        if prompt.startswith("Signals:"):
            return ClusterOutput(
                clusters=[
                    Cluster(
                        title=f"C{i}", summary="s", category="economy", signal_ids=[f"S{i + 1}"]
                    )
                    for i in range(n)
                ]
            )
        return scorer(prompt)

    return function_model_returning(out)


def title_of(prompt):
    return next(w for w in prompt.split() if w.startswith("C") and w[1:].isdigit())


def test_stage_attrs():
    s = SynthesizeStage()
    assert (s.name, s.output_name, s.output_type) == ("synthesize", "topics", TopicList)


async def test_stage_returns_top_n_ranked(ctx):
    scorer = lambda p: score_dict(da=int(title_of(p)[1:]) % 5 + 1)  # noqa: E731
    stage = SynthesizeStage(model=stage_model(7, scorer))
    res = await stage.run(ctx, radar(7))
    assert len(res.topics) == 5
    finals = [t.final_score for t in res.topics]
    assert finals == sorted(finals, reverse=True)
    assert not res.budget_exhausted and res.dropped == []


async def test_budget_exhaustion_keeps_partial(ctx):
    ctx.config.concurrency = 1
    calls = []

    def scorer(p):
        calls.append(1)
        if len(calls) == 2:
            ctx.tracer.total_cost = ctx.tracer.run_budget_usd  # budget gone after this call
        return score_dict()

    res = await SynthesizeStage(model=stage_model(5, scorer)).run(ctx, radar(5))
    assert len(res.topics) == 2 and res.budget_exhausted
    assert len(calls) == 2
    assert res.dropped == [f"C{i}: not scored (budget)" for i in (2, 3, 4)]


async def test_scoring_failure_recorded_in_dropped(ctx):
    def scorer(p):
        if title_of(p) == "C1":
            raise RuntimeError("model down")
        return score_dict()

    res = await SynthesizeStage(model=stage_model(3, scorer)).run(ctx, radar(3))
    assert sorted(t.title for t in res.topics) == ["C0", "C2"]
    assert res.dropped == ["C1: scoring failed: model down"]


async def test_llm_score_out_of_range_is_dropped(ctx):  # retry exhausted -> dropped
    def scorer(p):
        return score_dict(da=7) if title_of(p) == "C0" else score_dict()

    res = await SynthesizeStage(model=stage_model(2, scorer)).run(ctx, radar(2))
    assert [t.title for t in res.topics] == ["C1"]
    assert len(res.dropped) == 1 and res.dropped[0].startswith("C0: scoring failed:")


async def test_cluster_budget_error_propagates(ctx):
    ctx.tracer.total_cost = ctx.tracer.run_budget_usd
    with pytest.raises(BudgetExceeded):
        await SynthesizeStage(model=stage_model(1, lambda p: score_dict())).run(ctx, radar(1))


async def test_cluster_drops_carried_into_topic_list(ctx):
    def out(prompt):
        return ClusterOutput(
            clusters=[Cluster(title="Ghost", summary="s", category="law", signal_ids=["zz"])]
        )

    res = await SynthesizeStage(model=function_model_returning(out)).run(ctx, radar(1))
    assert res.topics == [] and res.dropped == ["Ghost: no valid signals"]


def test_describe_counts():
    from kenya_data_engine.models import TopicList

    out = TopicList(topics=[], dropped=["a", "b"])
    assert SynthesizeStage().describe(out) == "0 topics · 2 dropped"


async def test_stage_blends_topic_memory_novelty(ctx):
    from kenya_data_engine.data.periods import today_nairobi
    from kenya_data_engine.research.memory import TopicMemory

    TopicMemory(ctx.home.db_path).record("Fuel prices rise", "briefs/x", today_nairobi())
    titles = ["Fuel prices rise", "Rice imports"]

    def out(prompt):
        if prompt.startswith("Signals:"):
            return ClusterOutput(
                clusters=[
                    Cluster(title=t, summary="s", category="economy", signal_ids=[f"S{i + 1}"])
                    for i, t in enumerate(titles)
                ]
            )
        return score_dict(novelty=5)

    res = await SynthesizeStage(model=function_model_returning(out)).run(ctx, radar(2))
    assert {t.title: t.scores.novelty for t in res.topics} == {
        "Fuel prices rise": 1,
        "Rice imports": 5,
    }
