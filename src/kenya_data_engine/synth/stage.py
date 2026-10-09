"""SynthesizeStage: cluster radar signals, score clusters, rank in code."""

import asyncio

from pydantic_ai.models import Model

from kenya_data_engine.context import RunContext
from kenya_data_engine.data.periods import today_nairobi
from kenya_data_engine.errors import BudgetExceeded
from kenya_data_engine.models import RadarResult, Topic, TopicList
from kenya_data_engine.research.memory import TopicMemory
from kenya_data_engine.synth.cluster import Cluster, cluster_signals
from kenya_data_engine.synth.score import rank_topics, score_cluster


class SynthesizeStage:
    name = "synthesize"
    output_name = "topics"
    output_type = TopicList

    def __init__(self, model: Model | None = None) -> None:
        self.model = model

    def describe(self, output: TopicList) -> str:
        return f"{len(output.topics)} topics · {len(output.dropped)} dropped"

    async def run(self, ctx: RunContext, inp: RadarResult) -> TopicList:
        clusters, dropped = await cluster_signals(inp.signals, ctx, model=self.model)
        by_id = {s.id: s for s in inp.signals}
        memory, today = TopicMemory(ctx.home.db_path), today_nairobi()
        sem = asyncio.Semaphore(max(ctx.config.concurrency, 1))
        exhausted = False
        topics: list[Topic] = []
        failures: dict[int, str] = {}
        unscored: dict[int, str] = {}

        async def one(i: int, cluster: Cluster) -> None:
            nonlocal exhausted
            async with sem:
                if exhausted:
                    unscored[i] = f"{cluster.title}: not scored (budget)"
                    return
                try:
                    topics.append(
                        await score_cluster(
                            cluster, by_id, ctx, model=self.model, topic_memory=memory, today=today
                        )
                    )
                except BudgetExceeded:
                    exhausted = True
                    unscored[i] = f"{cluster.title}: not scored (budget)"
                except Exception as exc:
                    failures[i] = f"{cluster.title}: scoring failed: {exc}"

        await asyncio.gather(*(one(i, c) for i, c in enumerate(clusters)))
        dropped = dropped + [(failures | unscored)[i] for i in sorted(failures | unscored)]
        return TopicList(
            topics=rank_topics(topics, ctx.config.top_n),
            dropped=dropped,
            budget_exhausted=exhausted,
        )
