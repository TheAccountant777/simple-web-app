import pytest
from conftest import function_model_returning

from kenya_data_engine.llm import load_prompt
from kenya_data_engine.models import Signal, Topic, TopicScores, signal_id
from kenya_data_engine.synth.cluster import Cluster
from kenya_data_engine.synth.score import ScoreOutput, rank_topics, score_cluster, weighted_score

DEFAULT_WEIGHTS = {
    "data_ability": 0.35,
    "wallet_impact": 0.20,
    "timeliness": 0.20,
    "clarity_gap": 0.15,
    "novelty": 0.10,
}


def scores(**kw):
    base = dict(data_ability=3, wallet_impact=3, timeliness=3, clarity_gap=3, novelty=3)
    return TopicScores(**{**base, **kw}, justification={})


def topic(title, final, n=1):
    return Topic(
        id=title,
        title=title,
        summary="s",
        why_now="w",
        category="economy",
        signal_ids=[str(i) for i in range(n)],
        scores=scores(),
        final_score=final,
    )


def test_weighted_score_uses_spec_weights():
    s = TopicScores(
        data_ability=5, wallet_impact=4, timeliness=3, clarity_gap=2, novelty=1, justification={}
    )
    assert weighted_score(s, DEFAULT_WEIGHTS) == pytest.approx(
        0.35 * 5 + 0.20 * 4 + 0.20 * 3 + 0.15 * 2 + 0.10 * 1
    )


def test_weighted_score_rounds_to_three_places():
    assert weighted_score(scores(), {**DEFAULT_WEIGHTS, "novelty": 0.1000001}) == 3.0


def test_rank_ties_break_on_signal_count_then_title():
    ts = [topic("b", 3.0, 1), topic("a", 3.0, 1), topic("c", 3.0, 2), topic("z", 4.0, 1)]
    assert [t.title for t in rank_topics(ts, 3)] == ["z", "c", "a"]


def test_score_output_range():
    with pytest.raises(ValueError):
        ScoreOutput(
            data_ability=7,
            wallet_impact=1,
            timeliness=1,
            clarity_gap=1,
            novelty=1,
            justification={},
            why_now="w",
        )


async def test_score_cluster_computes_final_in_code(ctx):
    seen = []

    def out(prompt):
        seen.append(prompt)
        # a rogue extra field must not be able to set the final score
        return {
            "data_ability": 5,
            "wallet_impact": 4,
            "timeliness": 3,
            "clarity_gap": 2,
            "novelty": 1,
            "justification": {"data_ability": "official CPI"},
            "why_now": "CPI out Friday",
            "final_score": 99,
        }

    c = Cluster(title="Fuel", summary="s", category="personal_finance", signal_ids=["a1"])
    sigs = {
        "a1": Signal(
            id="a1", kind="news", title="EPRA fuel", source="x", url=None, published_at=None
        )
    }
    t = await score_cluster(c, sigs, ctx, model=function_model_returning(out))
    assert t.id == signal_id(None, "Fuel|a1")
    assert t.final_score == weighted_score(t.scores, ctx.config.weights) == 3.55
    assert t.why_now == "CPI out Friday" and t.category == "personal_finance"
    assert "Fuel" in seen[0] and "S1 | news" in seen[0]


def test_score_prompt_has_primer_and_rubric():
    text = load_prompt("score")
    assert "{{primer}}" not in text
    assert "Kenya context" in text
    for key in DEFAULT_WEIGHTS:
        assert key in text
    assert "25 words" in text and "do not compute totals" in text


async def test_same_title_clusters_get_unique_ids(ctx):
    out = {
        "data_ability": 3, "wallet_impact": 3, "timeliness": 3, "clarity_gap": 3,
        "novelty": 3, "justification": {}, "why_now": "w",
    }  # fmt: skip
    fm = function_model_returning(out)
    sigs = {
        i: Signal(id=i, kind="news", title=i, source="x", url=None, published_at=None)
        for i in ("a", "b")
    }
    t1 = await score_cluster(
        Cluster(title="Same", summary="s", category="law", signal_ids=["a"]), sigs, ctx, model=fm
    )
    t2 = await score_cluster(
        Cluster(title="Same", summary="s", category="law", signal_ids=["b"]), sigs, ctx, model=fm
    )
    assert t1.id != t2.id


async def test_score_blends_novelty(ctx):
    from datetime import date

    from kenya_data_engine.research.memory import TopicMemory

    mem = TopicMemory(ctx.home.db_path)
    mem.record("Fuel prices rise", "briefs/x", date(2026, 10, 1))
    out = {
        "data_ability": 5, "wallet_impact": 4, "timeliness": 3, "clarity_gap": 2,
        "novelty": 5, "justification": {"novelty": "fresh"}, "why_now": "w",
    }  # fmt: skip
    c = Cluster(title="Fuel prices rise again", summary="s", category="law", signal_ids=["a1"])
    sigs = {"a1": Signal(id="a1", kind="news", title="t", source="x", url=None, published_at=None)}
    fm = function_model_returning(out)
    plain = await score_cluster(c, sigs, ctx, model=fm)
    blended = await score_cluster(c, sigs, ctx, model=fm, topic_memory=mem, today=date(2026, 10, 9))
    assert plain.scores.novelty == 5
    assert blended.scores.novelty == 1  # covered 8 days ago: capped at 1
    assert blended.scores.justification["novelty"] == "fresh (covered 8 days ago)"
    assert blended.final_score == weighted_score(blended.scores, ctx.config.weights)
    assert blended.final_score < plain.final_score
    # an older coverage band caps at 3; an unrelated title leaves the LLM score alone
    mem2 = TopicMemory(ctx.home.db_path)
    older = await score_cluster(c, sigs, ctx, model=fm, topic_memory=mem2, today=date(2026, 10, 25))
    assert older.scores.novelty == 3
    other = Cluster(title="Rice imports", summary="s", category="law", signal_ids=["a1"])
    kept = await score_cluster(
        other, sigs, ctx, model=fm, topic_memory=mem2, today=date(2026, 10, 9)
    )
    assert kept.scores.novelty == 5 and kept.scores.justification["novelty"] == "fresh"
