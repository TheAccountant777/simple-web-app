"""Topic and run builders for the compare tests."""

from kenya_data_engine.models import Topic, TopicList, TopicScores
from kenya_data_engine.runs import RunHandle


def topic(tid, title, signals, *, score=4.0, category="economy", **crit):
    base = dict(
        data_ability=3, wallet_impact=3, timeliness=3, clarity_gap=3, novelty=3, justification={}
    )
    return Topic(
        id=tid,
        title=title,
        summary="s",
        why_now="w",
        category=category,
        signal_ids=list(signals),
        scores=TopicScores(**{**base, **crit}),
        final_score=score,
    )


def topic_list(*topics):
    return TopicList(topics=list(topics), dropped=[])


def write_topics(runs_dir, run_id, *topics):
    path = runs_dir / run_id
    path.mkdir(parents=True, exist_ok=True)
    run = RunHandle(run_id, path)
    run.write("topics", topic_list(*topics))
    return run
