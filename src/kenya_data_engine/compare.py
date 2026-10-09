"""Multi-run consensus: which topics keep coming back, and how steady are their scores.

Pure maths over finished runs' `TopicList`s (no LLM, no I/O apart from `load_topic_runs`).
Topics are matched across runs by the overlap of their signal ids, which are URL-based and so
stable between runs; titles are only a fallback.
"""

import re
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from kenya_data_engine.models import Topic, TopicList
from kenya_data_engine.runs import RunStore

CRITERIA = ("data_ability", "wallet_impact", "timeliness", "clarity_gap", "novelty")
TITLE_MIN_JACCARD = 0.5
STRONG_RANGE = 0.5
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "with",
        "after",
        "over",
        "new",
        "says",
        "say",
        "kenya",
        "kenyans",
    ]
)


@dataclass(frozen=True)
class TopicObs:
    run_id: str
    topic: Topic
    rank: int  # 1-based position in that run's ranking


class CriterionStat(BaseModel):
    mean: float
    min: int
    max: int


class Member(BaseModel):
    run_id: str
    topic_id: str
    title: str
    score: float
    rank: int


class ConsensusTopic(BaseModel):
    key: str
    title: str
    category: str
    appearances: int
    runs: int
    mean_score: float
    min_score: float
    max_score: float
    mean_rank: float
    criteria: dict[str, CriterionStat]
    run_ids: list[str]
    members: list[Member] = Field(default_factory=list)
    stability: Literal["strong", "mixed", "noise"]


def _tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _affinity(topic: Topic, member: Topic, min_jaccard: float) -> float:
    """0 when the two are not the same story, else a score where higher means more alike."""
    sig = _jaccard(frozenset(topic.signal_ids), frozenset(member.signal_ids))
    if sig >= min_jaccard and sig > 0:
        return sig
    title = _jaccard(_tokens(topic.title), _tokens(member.title))
    return title if title >= TITLE_MIN_JACCARD else 0.0


def match_topics(
    runs: list[tuple[str, TopicList]], *, min_jaccard: float = 0.3
) -> list[list[TopicObs]]:
    """Greedy cross-run grouping; at most one topic per run in a group.

    Runs are taken in the order given (oldest first) and topics by rank, so the result is
    deterministic. Within a run, the strongest topic-to-group overlaps are assigned first.
    """
    groups: list[list[TopicObs]] = []
    for run_id, topic_list in runs:
        observed = [TopicObs(run_id, t, rank) for rank, t in enumerate(topic_list.topics, 1)]
        pairs: list[tuple[float, int, int]] = []
        for oi, obs in enumerate(observed):
            for gi, group in enumerate(groups):
                best = max(_affinity(obs.topic, m.topic, min_jaccard) for m in group)
                if best > 0:
                    pairs.append((best, oi, gi))
        pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
        taken_obs: set[int] = set()
        taken_groups: set[int] = set()
        additions: dict[int, TopicObs] = {}
        for _, oi, gi in pairs:
            if oi in taken_obs or gi in taken_groups:
                continue
            taken_obs.add(oi)
            taken_groups.add(gi)
            additions[gi] = observed[oi]
        for gi, obs in additions.items():
            groups[gi].append(obs)
        groups.extend([obs] for oi, obs in enumerate(observed) if oi not in taken_obs)
    return groups


def _title_of(group: list[TopicObs]) -> str:
    counts = Counter(o.topic.title for o in group)
    top = max(counts.values())
    best = max(
        (o for o in group if counts[o.topic.title] == top), key=lambda o: o.topic.final_score
    )
    return best.topic.title


def _stability(appearances: int, runs: int, spread: float) -> Literal["strong", "mixed", "noise"]:
    if appearances == runs and runs >= 2 and spread <= STRONG_RANGE:
        return "strong"
    if appearances == 1 and runs >= 3:
        return "noise"
    return "mixed"


def consensus(
    runs: list[tuple[str, TopicList]], *, min_jaccard: float = 0.3
) -> list[ConsensusTopic]:
    n = len(runs)
    out: list[ConsensusTopic] = []
    for group in match_topics(runs, min_jaccard=min_jaccard):
        scores = [o.topic.final_score for o in group]
        criteria = {}
        for key in CRITERIA:
            vals = [getattr(o.topic.scores, key) for o in group]
            criteria[key] = CriterionStat(mean=statistics.fmean(vals), min=min(vals), max=max(vals))
        out.append(
            ConsensusTopic(
                key=group[0].topic.id,
                title=_title_of(group),
                category=Counter(o.topic.category for o in group).most_common(1)[0][0],
                appearances=len(group),
                runs=n,
                mean_score=statistics.fmean(scores),
                min_score=min(scores),
                max_score=max(scores),
                mean_rank=statistics.fmean(o.rank for o in group),
                criteria=criteria,
                run_ids=[o.run_id for o in group],
                members=[
                    Member(
                        run_id=o.run_id,
                        topic_id=o.topic.id,
                        title=o.topic.title,
                        score=o.topic.final_score,
                        rank=o.rank,
                    )
                    for o in group
                ],
                stability=_stability(len(group), n, max(scores) - min(scores)),
            )
        )
    out.sort(key=lambda c: (-c.appearances, -c.mean_score, c.title))
    return out


def load_topic_runs(
    store: RunStore, run_ids: list[str]
) -> tuple[list[tuple[str, TopicList]], list[str]]:
    """Runs (oldest first) that have a topic list, plus the ids skipped as incomplete."""
    loaded: list[tuple[str, TopicList]] = []
    skipped: list[str] = []
    for rid in sorted(set(run_ids)):
        topics = store.open(rid).read("topics", TopicList)
        if topics is None:
            skipped.append(rid)
        else:
            loaded.append((rid, topics))
    return loaded, skipped
