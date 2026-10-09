import pytest
from compare_factory import topic, topic_list

from kenya_data_engine.compare import consensus, match_topics


def two_runs():
    a = topic_list(
        topic("a1", "Fuel prices jump", ["s1", "s2"], score=4.2, wallet_impact=5),
        topic("a2", "M-Pesa fee changes", ["s9"], score=3.0),
    )
    b = topic_list(
        topic(
            "b1",
            "Petrol cost rise hits matatu fares",
            ["s2", "s3", "s1"],
            score=3.8,
            wallet_impact=3,
        ),
        topic("b2", "Housing levy", ["s20"], score=3.5),
    )
    return [("run-1", a), ("run-2", b)]


def test_same_topic_with_different_titles_matches_on_shared_signals():
    groups = match_topics(two_runs())
    fuel = next(g for g in groups if g[0].topic.id == "a1")
    assert [o.run_id for o in fuel] == ["run-1", "run-2"]
    assert [o.topic.id for o in fuel] == ["a1", "b1"]


def test_disjoint_topics_are_not_matched():
    groups = match_topics(two_runs())
    assert len(groups) == 3  # fuel (2 obs) + the two singletons
    singles = sorted(g[0].topic.id for g in groups if len(g) == 1)
    assert singles == ["a2", "b2"]


def test_group_holds_one_topic_per_run_keeping_the_better_overlap():
    a = topic_list(topic("a1", "Fuel", ["s1", "s2", "s3"]))
    b = topic_list(
        topic("b1", "Fuel partial", ["s1", "x1", "x2", "x3"]),
        topic("b2", "Fuel better", ["s1", "s2", "s3", "s4"]),
    )
    groups = match_topics([("r1", a), ("r2", b)])
    fuel = next(g for g in groups if g[0].topic.id == "a1")
    assert [o.topic.id for o in fuel] == ["a1", "b2"]
    assert any(g[0].topic.id == "b1" and len(g) == 1 for g in groups)
    for g in groups:
        assert len({o.run_id for o in g}) == len(g)


def test_title_fallback_when_there_are_no_signals():
    a = topic_list(topic("a1", "Central Bank rate decision", []))
    b = topic_list(topic("b1", "Central Bank rate decision today", []), topic("b2", "Other", []))
    groups = match_topics([("r1", a), ("r2", b)])
    assert [o.topic.id for o in next(g for g in groups if len(g) == 2)] == ["a1", "b1"]


def test_title_fallback_below_threshold_does_not_match():
    a = topic_list(topic("a1", "Fuel prices", ["s1"]))
    b = topic_list(topic("b1", "Housing levy debate", ["s2"]))
    assert all(len(g) == 1 for g in match_topics([("r1", a), ("r2", b)]))


def test_ranks_follow_list_order_and_matching_is_deterministic():
    runs = two_runs()
    first = match_topics(runs)
    assert match_topics(runs) == first
    fuel = next(g for g in first if len(g) == 2)
    assert [o.rank for o in fuel] == [1, 1]
    housing = next(g for g in first if g[0].topic.id == "b2")
    assert housing[0].rank == 2


def test_consensus_stats():
    c = consensus(two_runs())
    fuel = c[0]
    assert (fuel.appearances, fuel.runs) == (2, 2)
    assert fuel.mean_score == pytest.approx(4.0)
    assert (fuel.min_score, fuel.max_score) == (3.8, 4.2)
    assert fuel.mean_rank == 1.0
    assert fuel.criteria["wallet_impact"].mean == 4.0
    assert (fuel.criteria["wallet_impact"].min, fuel.criteria["wallet_impact"].max) == (3, 5)
    assert fuel.run_ids == ["run-1", "run-2"]
    assert fuel.category == "economy"
    assert fuel.title in {"Fuel prices jump", "Petrol cost rise hits matatu fares"}
    assert fuel.title == "Fuel prices jump"  # no repeated title: the highest-scored member wins
    assert [m.run_id for m in fuel.members] == ["run-1", "run-2"]


def test_consensus_ordering_and_stability_labels():
    runs = two_runs()
    runs.append(("run-3", topic_list(topic("c1", "Fuel", ["s1"], score=4.0))))
    c = consensus(runs)
    assert [t.appearances for t in c] == [3, 1, 1]
    assert c[0].stability == "strong"  # all 3 runs and scores 3.8-4.2 are within 0.5
    # ties on appearances sort by mean score (then title)
    assert [t.title for t in c[1:]] == ["Housing levy", "M-Pesa fee changes"]
    assert all(t.stability == "noise" for t in c[1:])  # seen once in >= 3 runs


def test_strong_requires_all_runs_and_a_tight_range():
    tight = [
        ("r1", topic_list(topic("a", "Fuel", ["s1"], score=4.0))),
        ("r2", topic_list(topic("b", "Fuel", ["s1"], score=4.4))),
    ]
    assert consensus(tight)[0].stability == "strong"
    wide = [
        ("r1", topic_list(topic("a", "Fuel", ["s1"], score=3.0))),
        ("r2", topic_list(topic("b", "Fuel", ["s1"], score=4.4))),
    ]
    assert consensus(wide)[0].stability == "mixed"
    once_of_two = consensus(two_runs())
    assert next(t for t in once_of_two if t.title == "Housing levy").stability == "mixed"


def test_most_frequent_title_wins():
    runs = [
        ("r1", topic_list(topic("a", "Fuel up", ["s1"], score=3.0))),
        ("r2", topic_list(topic("b", "Petrol surge", ["s1"], score=4.5))),
        ("r3", topic_list(topic("c", "Fuel up", ["s1"], score=3.0))),
    ]
    assert consensus(runs)[0].title == "Fuel up"


def test_empty_and_single_run():
    assert consensus([]) == []
    assert match_topics([]) == []
    single = consensus([("r1", topic_list(topic("a", "Fuel", ["s1"])))])
    assert len(single) == 1 and single[0].appearances == 1 and single[0].runs == 1
    assert single[0].stability == "mixed"
    assert consensus([("r1", topic_list())]) == []
