"""Shared fakes for the research-agent tests."""

from kenya_data_engine.data.registry import load_catalog
from kenya_data_engine.research.budget import Ledger
from kenya_data_engine.research.evidence import EvidenceBook
from kenya_data_engine.research.tools import ResearchDeps
from kenya_data_engine.tools.search import SearchResult


class FakeSearch:
    def __init__(self, results=None):
        self.results, self.calls = results or [], []

    async def search(self, query, n=5, domains=None, *, depth="basic"):
        self.calls.append((query, domains))
        return [SearchResult(title=t, url=u, snippet="s") for t, u in self.results]


class FakeMemory:
    def __init__(self, specs=()):
        self.specs = list(specs)

    def lookup(self, need, today):
        return list(self.specs)

    def search(self, query):
        return [("k", s) for s in self.specs]


def make_deps(ctx, tmp_path, *, search=None, memory=None, group="scouts"):
    return ResearchDeps(
        ctx=ctx,
        ledger=Ledger.from_config(ctx.config.research),
        group=group,
        book=EvidenceBook(tmp_path, ctx.config.research.tiers, redact=ctx.tracer.redact),
        catalog=load_catalog(ctx.home),
        memory=memory or FakeMemory(),
        search=search or FakeSearch(),
    )
