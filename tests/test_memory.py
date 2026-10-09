from datetime import date

from kenya_data_engine.research.memory import (
    SourceMemory,
    TopicMemory,
    code_novelty,
    need_signature,
)
from kenya_data_engine.research.models import DataNeed, DataSourceSpec

TODAY = date(2026, 10, 9)


def _need(**kw):
    base = dict(
        id="n1", kind="series", question="q", metric="Super Petrol",
        entities=["Nairobi", "Mombasa"], unit="KES/L", frequency="monthly",
    )  # fmt: skip
    return DataNeed(**{**base, **kw})


def _spec(url="https://www.epra.go.ke/p.xlsx", need="n7"):
    return DataSourceSpec(need=need, via="file", url=url, publisher="epra.go.ke", why="w")


def test_need_signature_normalises():
    a = need_signature(_need())
    assert a == "super petrol|mombasa,nairobi|kes/l|monthly"
    assert need_signature(_need(entities=["mombasa", "NAIROBI"])) == a
    fact = DataNeed(kind="fact", question="Legal stage of Finance Bill")
    assert need_signature(fact) == "legal stage of finance bill|||none"


def test_record_and_lookup(tmp_path):
    mem = SourceMemory(tmp_path / "e.db")
    need = _need()
    assert mem.lookup(need, TODAY) == []
    mem.record(need, _spec(), True, date(2026, 10, 1))
    mem.record(need, _spec("https://b.go.ke/x"), True, date(2026, 10, 5))
    mem.record(need, _spec("https://b.go.ke/x"), True, date(2026, 10, 6))
    got = mem.lookup(need, TODAY)
    assert [s.url for s in got] == ["https://b.go.ke/x", "https://www.epra.go.ke/p.xlsx"]
    assert all(s.need == "n1" for s in got)  # the asking need's id, not the recorded one
    assert mem.lookup(_need(unit="USD"), TODAY) == []
    rows = mem.entries()
    assert rows[0]["successes"] >= 1 and rows[0]["spec"]["publisher"] == "epra.go.ke"
    assert SourceMemory(tmp_path / "e.db").lookup(need, TODAY)  # persisted


def test_failures_suppress_lookup(tmp_path):
    mem = SourceMemory(tmp_path / "e.db")
    need = _need()
    mem.record(need, _spec(), True, TODAY)
    assert len(mem.lookup(need, TODAY)) == 1
    mem.record(need, _spec(), False, TODAY)  # 1 success, 1 failure: not better than even
    assert mem.lookup(need, TODAY) == []
    mem.record(need, _spec(), True, TODAY)
    assert len(mem.lookup(need, TODAY)) == 1


def test_expired_after_60_days(tmp_path):
    mem = SourceMemory(tmp_path / "e.db")
    need = _need()
    mem.record(need, _spec(), True, date(2026, 8, 10))
    assert len(mem.lookup(need, date(2026, 10, 9))) == 1  # exactly 60 days
    assert mem.lookup(need, date(2026, 10, 10)) == []  # 61 days


def test_search_word_overlap_and_forget(tmp_path):
    mem = SourceMemory(tmp_path / "e.db")
    mem.record(_need(), _spec(), True, TODAY)
    mem.record(
        _need(metric="CPI inflation", entities=[], unit="pct"),
        _spec("https://k.or.ke/c"),
        True,
        TODAY,
    )
    mem.record(_need(metric="rice"), _spec("https://z.ke/r"), False, TODAY)
    hits = mem.search("petrol prices in Nairobi")
    assert [k for k, _ in hits] == ["super petrol|mombasa,nairobi|kes/l|monthly"]
    assert mem.search("zzz") == [] and mem.search("") == []
    assert mem.search("rice") == []  # failing entries are not offered
    assert mem.forget("super petrol|mombasa,nairobi|kes/l|monthly") == 1
    assert mem.search("petrol") == [] and mem.forget("nope") == 0


def test_topic_memory_jaccard(tmp_path):
    tm = TopicMemory(tmp_path / "e.db")
    assert tm.last_covered("Fuel prices rise", TODAY) is None
    tm.record("Fuel prices rise in Kenya", "briefs/a", date(2026, 9, 1))
    tm.record("Fuel prices rise in Kenya again", "briefs/b", date(2026, 10, 1))
    tm.record("Finance Bill 2026 explained", "briefs/c", date(2026, 10, 8))
    assert tm.last_covered("Fuel prices rise in Kenya", TODAY) == 8  # most recent match wins
    assert tm.last_covered("Kenya fuel prices rise", TODAY) == 8  # word order is irrelevant
    assert tm.last_covered("Fuel taxes", TODAY) is None  # Jaccard 1/6 < 0.5
    assert tm.last_covered("Rice imports", TODAY) is None
    assert tm.last_covered("", TODAY) is None
    assert tm.last_covered("Fuel prices rise in Kenya", date(2026, 9, 1)) == 0


def test_code_novelty_bands():
    bands = [14, 30]
    assert code_novelty(None, bands) is None
    assert [code_novelty(d, bands) for d in (0, 14, 15, 30)] == [1, 1, 3, 3]
    assert code_novelty(31, bands) is None
