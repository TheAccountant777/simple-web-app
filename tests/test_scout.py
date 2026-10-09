from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from research_util import FakeMemory, FakeSearch, make_deps

from kenya_data_engine.research.models import DataNeed, DataSourceSpec
from kenya_data_engine.research.scout import scout, scout_instructions

GOOD = "https://www.knbs.or.ke/cpi.xlsx"
INVENTED = "https://www.knbs.or.ke/made-up.xlsx"


def _need(**kw):
    base = dict(id="n1", kind="series", question="CPI", metric="cpi", publishers=["knbs.or.ke"])
    return DataNeed(**{**base, **kw})


def _spec(url, need="n1", via="file", **kw):
    return DataSourceSpec(need=need, via=via, url=url, publisher="knbs.or.ke", why="w", **kw)


def _counting_model(output=None):
    calls = []

    def fn(messages, info):
        calls.append(1)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output or {})])

    return FunctionModel(fn), calls


async def test_registry_hint_skips_llm(ctx, tmp_path):
    model, calls = _counting_model()
    deps = make_deps(ctx, tmp_path)
    res = await scout(_need(series_hint="wb:FP.CPI.TOTL.ZG"), deps, model=model)
    assert calls == [] and not res.llm_used and res.registry_hit and not res.memory_hit
    assert [(s.via, s.registry_key, s.need) for s in res.specs] == [
        ("registry", "wb:FP.CPI.TOTL.ZG", "n1")
    ]
    # a disabled or unknown hint falls through (to the agent here)
    res = await scout(_need(series_hint="imf:cpi"), deps, model=model)
    assert calls == [1] and res.llm_used and not res.registry_hit


async def test_memory_hit_skips_llm(ctx, tmp_path):
    model, calls = _counting_model()
    remembered = _spec(GOOD, need="n9")
    deps = make_deps(ctx, tmp_path, memory=FakeMemory([remembered]))
    res = await scout(_need(), deps, model=model)
    assert (
        calls == []
        and res.memory_hit
        and not res.llm_used
        and res.specs == [remembered.model_copy(update={"need": "n1"})]
    )
    assert GOOD in deps.memory_urls  # the tools now accept it


async def test_agent_spec_with_invented_url_rejected(ctx, tmp_path):
    search = FakeSearch([("CPI", GOOD)])
    out = {
        "specs": [
            _spec(GOOD, need="N1").model_dump(mode="json"),
            _spec(INVENTED).model_dump(mode="json"),
            _spec(GOOD, need="n2").model_dump(mode="json"),
            DataSourceSpec(
                need="n1", via="registry", registry_key="made:up", publisher="p", why="w"
            ).model_dump(mode="json"),
        ]
    }

    def fn(messages, info):
        if any(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", [])):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])
        return ModelResponse(parts=[ToolCallPart("web_search", {"query": "kenya cpi"})])

    deps = make_deps(ctx, tmp_path, search=search)
    res = await scout(_need(), deps, hint="try xlsx", model=FunctionModel(fn))
    assert res.llm_used and [s.url for s in res.specs] == [GOOD]
    assert res.specs[0].need == "n1"  # label spelling normalised
    assert f"rejected: invented url {INVENTED}" in res.rejected
    assert any("other need" in r for r in res.rejected)
    assert any("registry key" in r for r in res.rejected)
    assert search.calls == [("kenya cpi", ["knbs.or.ke"])]  # need's publishers fill in domains


async def test_agent_failure_is_soft(ctx, tmp_path):
    def fn(messages, info):
        return ModelResponse(parts=[ToolCallPart("preview_table", {"url": GOOD})])

    res = await scout(_need(), make_deps(ctx, tmp_path), model=FunctionModel(fn))
    assert res.specs == [] and res.rejected[0].startswith("scout failed:")


def test_scout_prompt_rules():
    text = scout_instructions(__import__("datetime").date(2026, 10, 9))
    assert "2026-10-09" in text and "{{" not in text and "Kenya context" in text
    for needle in ("at most 4 times", "Never invent", "preview_table", "untrusted", "empty list"):
        assert needle in text


async def test_memory_specs_are_vetted_and_fall_through(ctx, tmp_path):
    dead = DataSourceSpec(
        need="n1", via="registry", registry_key="imf:cpi", publisher="imf", why="w"
    )  # disabled in the catalog
    good = _spec(GOOD)
    model, calls = _counting_model({"specs": []})
    deps = make_deps(ctx, tmp_path, memory=FakeMemory([dead]))
    res = await scout(_need(), deps, model=model)
    assert calls == [1] and res.llm_used and not res.memory_hit and res.specs == []
    assert any("unknown registry key" in r for r in res.rejected)
    deps = make_deps(ctx, tmp_path, memory=FakeMemory([dead, good]))
    res = await scout(_need(), deps, model=model)
    assert calls == [1] and res.memory_hit and res.specs == [good]
    assert len(res.rejected) == 1


async def test_rejected_memory_urls_do_not_become_fetchable(ctx, tmp_path):
    from kenya_data_engine.research.tools import url_allowed

    other = _spec("https://www.knbs.or.ke/other.xlsx", need="n7")
    dead = DataSourceSpec(
        need="n1", via="registry", registry_key="imf:cpi", url=INVENTED, publisher="p", why="w"
    )
    model, _ = _counting_model({"specs": []})
    deps = make_deps(ctx, tmp_path, memory=FakeMemory([dead, other]))
    res = await scout(_need(), deps, model=model)
    assert res.memory_hit and [s.url for s in res.specs] == [other.url]
    assert url_allowed(deps, other.url) and not url_allowed(deps, INVENTED)
