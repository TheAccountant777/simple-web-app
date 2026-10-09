import json
from datetime import date

import pytest
from conftest import function_model_returning
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from kenya_data_engine.errors import BudgetExceeded
from kenya_data_engine.research.claims import ClaimsOutput
from kenya_data_engine.research.models import CandidateClaim
from kenya_data_engine.research.orchestrator import research
from kenya_data_engine.research.planner import TopicInput
from kenya_data_engine.research.verify import ChallengeOutput

TOPIC = TopicInput(title="Kenya inflation", summary="Prices", signals=["a — https://x.ke/a"])
WB_URL = "https://api.worldbank.org/v2/country/KEN/indicator/FP.CPI.TOTL.ZG"
WB_BODY = [
    {"page": 1},
    [
        {"date": "2025", "value": 7.67, "indicator": {"value": "Inflation"}},
        {"date": "2024", "value": 7.66, "indicator": {"value": "Inflation"}},
    ],
]


def brief_dict(needs=None, verdict="supported", **over):
    base = {
        "topic": "Kenya inflation",
        "core_question": "How high is inflation?",
        "angles": [{"label": "a", "thesis": "t", "contrarian": True}],
        "framing_challenge": "Maybe it is falling",
        "chart_concepts": [{"id": "c1", "relationship": "change_over_time", "idea": "cpi"}],
        "data_needs": needs
        if needs is not None
        else [
            {
                "kind": "series",
                "question": "Kenya CPI inflation",
                "metric": "inflation",
                "entities": ["Kenya"],
                "frequency": "annual",
                "min_points": 2,
                "priority": 1,
                "series_hint": "wb:FP.CPI.TOTL.ZG",
            }
        ],
        "verdict": verdict,
    }
    return {**base, **over}


def claim_model():
    cand = CandidateClaim(
        text_template="Kenya's consumer price inflation was {F1} in 2025.",
        figures=["F1"],
        claim_type="annual",
        entity="Kenya",
        metric="inflation",
        period="2025",
    )
    return function_model_returning(ClaimsOutput(claims=[cand]))


class Counter:
    """A FunctionModel that counts calls and answers with a fixed output."""

    def __init__(self, output):
        self.calls, self.prompts = 0, []
        outer = self

        def fn(messages, info):
            outer.calls += 1
            outer.prompts.append(
                "\n".join(str(getattr(p, "content", "")) for m in messages for p in m.parts)
            )
            out = output(outer.calls) if callable(output) else output
            if isinstance(out, Exception):
                raise out
            args = out.model_dump() if hasattr(out, "model_dump") else out
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

        self.model = FunctionModel(fn)


def models(brief=None, scout=None, **extra):
    planner = Counter(brief or brief_dict())
    out = {
        "planner": planner.model,
        "scout": (scout or Counter({"specs": []})).model,
        "claims": claim_model(),
        "challenge": function_model_returning(ChallengeOutput()),
    }
    out.update(extra)
    return out, planner


async def test_research_happy_path_world_bank(ctx, respx_mock):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    scout_model = Counter({"specs": []})
    m, _ = models(scout=scout_model)
    events = []
    out = await research(TOPIC, ctx, models=m, on_event=lambda k, p: events.append((k, p)))
    assert scout_model.calls == 0  # the registry hint resolved the need
    assert out.statuses[0].status == "satisfied" and out.statuses[0].figure_ids
    assert out.figures is not None and out.figures.refs
    (claim,) = out.claims
    assert claim.status == "fact", claim.reasons
    assert "7.7" in claim.text and claim.figure_ids
    assert out.verdict == "supported" and out.stopped_because == "all needs satisfied"
    sc = out.scorecard
    assert (sc.facts, sc.needs_total, sc.needs_satisfied, sc.registry_hits) == (1, 1, 1, 1)
    assert sc.credits_spent == 0 and sc.gap_rate == 0.0
    kinds = {k for k, _ in events}
    assert {"step", "need", "claim", "ledger"} <= kinds
    json.dumps([p for k, p in events if k == "step"])  # step payloads are plain data
    run = ctx.run.dir / "research"
    for name in ("topic", "plan", "rounds", "figures", "claims", "verify", "challenge", "outcome"):
        assert (run / f"{name}.json").exists(), name
    assert (run / "evidence.json").exists()
    assert out.model_dump_json()  # serialisable


async def test_reject_stops_early(ctx, respx_mock):
    reject = brief_dict(needs=[], verdict="reject", verdict_reasons=["no numbers exist"])
    m, planner = models(reject)
    scout_model = Counter({"specs": []})
    m["scout"] = scout_model.model
    out = await research(TOPIC, ctx, models=m)
    assert planner.calls == 1 and scout_model.calls == 0
    assert out.verdict == "reject" and out.verdict_reasons == ["no numbers exist"]
    assert out.stopped_because == "rejected by the planner" and out.claims == []
    assert not (ctx.run.dir / "research" / "rounds.json").exists()


async def test_reject_with_force_continues(ctx, respx_mock):
    reject = brief_dict(needs=[], verdict="reject", verdict_reasons=["thin"])
    m, _ = models(reject)
    out = await research(TOPIC, ctx, models=m, force=True)
    assert out.verdict == "reject" and out.stopped_because == "no data needs"


async def test_plan_only(ctx, respx_mock):
    m, planner = models()
    out = await research(TOPIC, ctx, models=m, plan_only=True)  # no HTTP mocked: none is made
    assert planner.calls == 1 and out.stopped_because == "plan only"
    assert out.verdict == "supported" and out.statuses == [] and out.scorecard.gap_rate == 0.0


def two_needs():
    return brief_dict(
        needs=[
            {
                "kind": "series",
                "question": "Kenya CPI inflation",
                "metric": "inflation",
                "entities": ["Kenya"],
                "frequency": "annual",
                "min_points": 2,
                "series_hint": "wb:FP.CPI.TOTL.ZG",
            },
            {"kind": "fact", "question": "What did the MPC decide?", "priority": 3},
        ]
    )


async def test_round_two_only_open_needs(ctx, respx_mock):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    scout_model = Counter({"specs": []})
    m, _ = models(two_needs(), scout=scout_model)
    out = await research(TOPIC, ctx, models=m)
    # n1 is satisfied by the registry hit (no LLM); n2 is scouted in round 1 and again in round 2
    assert scout_model.calls == 2
    assert "Need id: n2" in scout_model.prompts[1] and "Need id: n1" not in scout_model.prompts[1]
    assert "Hint from an earlier round" in scout_model.prompts[1]
    assert [s.status for s in out.statuses] == ["satisfied", "not_found"]
    assert out.stopped_because == "last round added no new points or evidence"
    assert any(g.startswith("n2 not_found") for g in out.gaps)


async def test_stop_no_progress_and_max_rounds(ctx, respx_mock, monkeypatch):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    scout_model = Counter({"specs": []})
    m, _ = models(two_needs(), scout=scout_model)
    ctx.config.research.max_rounds = 1
    out = await research(TOPIC, ctx, models=m)
    assert scout_model.calls == 1 and out.stopped_because == "max rounds (1) reached"


async def test_budget_exhausted_mid_rounds_still_writes_outcome(ctx, respx_mock):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    scout_model = Counter(BudgetExceeded("scouts budget exhausted"))
    m, _ = models(two_needs(), scout=scout_model)
    out = await research(TOPIC, ctx, models=m)
    assert out.stopped_because == "budget exhausted during scouting"
    assert [s.status for s in out.statuses] == ["satisfied", "not_found"]
    assert any("not scouted" in g for g in out.gaps)
    assert out.claims  # claims still ran on what exists
    assert (ctx.run.dir / "research" / "outcome.json").exists()
    assert out.scorecard.stopped_because == out.stopped_because


async def test_budget_exhausted_in_claims_continues(ctx, respx_mock):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    m, _ = models()
    m["claims"] = Counter(BudgetExceeded("claims budget exhausted")).model
    out = await research(TOPIC, ctx, models=m)
    assert out.claims == [] and any("claim writer stopped" in g for g in out.gaps)
    assert out.verdict != "supported"  # the headline could not be a fact


async def test_scout_exception_recorded_in_gaps(ctx, respx_mock):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    scout_model = Counter(RuntimeError("boom sk-secret"))
    m, _ = models(two_needs(), scout=scout_model)
    out = await research(TOPIC, ctx, models=m)
    assert any(g.startswith("n2: scout failed: boom") for g in out.gaps)
    assert out.statuses[0].status == "satisfied"  # the other need carried on
    assert out.claims


async def test_resume_skips_finished_steps(ctx, respx_mock):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    m, _ = models()
    first = await research(TOPIC, ctx, models=m)
    run = ctx.run.dir / "research"
    for name in ("outcome", "challenge"):
        (run / f"{name}.json").unlink()
    boom = Counter(RuntimeError("must not be called"))
    challenge_model = Counter(ChallengeOutput())
    m2 = {
        "planner": boom.model,
        "scout": boom.model,
        "claims": boom.model,
        "entail": boom.model,
        "challenge": challenge_model.model,
    }
    again = await research(TOPIC, ctx, models=m2, resume=True)
    assert boom.calls == 0 and challenge_model.calls == 1
    assert again.claims == first.claims and again.verdict == first.verdict
    assert [s.status for s in again.statuses] == ["satisfied"]
    assert again.statuses[0].figure_ids == first.statuses[0].figure_ids


async def test_resume_without_checkpoints_runs_everything(ctx, respx_mock):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    m, planner = models()
    await research(TOPIC, ctx, models=m, resume=True)
    assert planner.calls == 1


@pytest.mark.parametrize("today", [date(2026, 10, 9)])
async def test_source_memory_recorded(ctx, respx_mock, today):
    from kenya_data_engine.research.memory import SourceMemory

    respx_mock.get(WB_URL).respond(json=WB_BODY)
    m, _ = models()
    await research(TOPIC, ctx, models=m)
    entries = SourceMemory(ctx.home.db_path).entries()
    assert len(entries) == 1 and entries[0]["successes"] == 1
