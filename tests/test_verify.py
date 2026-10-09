from datetime import date
from decimal import Decimal

import pytest
from conftest import function_model_returning
from research_util import make_deps

from kenya_data_engine.data.stats import Figure, FigureBook
from kenya_data_engine.research.claims import ClaimsOutput
from kenya_data_engine.research.figures import FigurePack
from kenya_data_engine.research.models import (
    Angle,
    CandidateClaim,
    ChartConcept,
    Claim,
    DataNeed,
    FigureRef,
    NeedStatus,
    ResearchBrief,
)
from kenya_data_engine.research.verify import (
    assign_status,
    challenge,
    detect_conflicts,
    entail,
    recheck_verdict,
    verify_static,
)

TODAY = date(2026, 10, 9)
PAGE = (
    "Maximum retail pump prices from 15 September 2026. In Nairobi, super petrol retails at a "
    "maximum of KSh 198.00 per litre. Value Added Tax on fuel is 16 per cent."
)
Q = "In Nairobi, super petrol retails at a maximum of KSh 198.00 per litre"
T1, T3, T4 = "https://www.epra.go.ke/p", "https://www.nation.africa/a", "https://blog.example.com/b"


def _book(ctx, tmp_path, url=T1, published=date(2026, 9, 15), text=PAGE):
    deps = make_deps(ctx, tmp_path)
    deps.book.add_text(url, text, None, published)
    return deps


def _cand(**kw):
    base = dict(
        text_template="Super petrol in Nairobi costs KSh 198.00 per litre.", quote=Q,
        evidence="E1", claim_type="price", entity="Nairobi", metric="super price",
        period="2026-09",
    )  # fmt: skip
    return CandidateClaim(**{**base, **kw})


def _static(ctx, deps, cands, pack=None, fbook=None):
    pack = pack or FigurePack(refs=[], markdown="", comparisons_csv="")
    return verify_static(
        cands, deps.book, pack, fbook or FigureBook(),
        tiers=ctx.config.research.tiers, stale_days=ctx.config.research.stale_days, today=TODAY,
    )  # fmt: skip


def _fact(ctx, deps, cands, **kw):
    """Static checks, then an entailment of yes by hand, then the status rules."""
    claims = _static(ctx, deps, cands, **kw)
    for c in claims:
        if c.status != "refused" and c.entailment == "not_run":
            c.entailment = "yes"
    return assign_status(claims, detect_conflicts(claims))


def test_literal_number_must_be_in_quote(ctx, tmp_path):
    deps = _book(ctx, tmp_path)
    ok, bad = _static(
        ctx, deps, [_cand(), _cand(text_template="Super petrol in Nairobi costs KSh 200.00.")]
    )
    assert ok.reasons == [] and ok.numbers_ok and ok.status == "inference" and ok.quote_grounded
    assert not bad.numbers_ok and bad.status == "refused"
    assert any("200.00" in r for r in bad.reasons)


def test_year_in_period_allowed(ctx, tmp_path):
    deps = _book(ctx, tmp_path)
    t = "Super petrol in Nairobi costs KSh 198.00 per litre in 2026."
    ok, other = _static(ctx, deps, [_cand(text_template=t), _cand(text_template=t, period="2025")])
    assert ok.numbers_ok and ok.status == "inference"
    assert not other.numbers_ok and other.status == "refused"


def test_quote_not_in_text_refused(ctx, tmp_path):
    deps = _book(ctx, tmp_path)
    c, no_ev = _static(
        ctx,
        deps,
        [_cand(quote="In Nairobi petrol retails at KSh 198.00 per litre"), _cand(evidence="E9")],
    )
    assert c.status == "refused" and not c.quote_grounded
    assert no_ev.status == "refused" and "unknown evidence label" in no_ev.reasons[0]


def test_entity_mismatch_refused(ctx, tmp_path):
    deps = _book(ctx, tmp_path)
    entity, period, none = _static(
        ctx, deps, [_cand(entity="Mombasa"), _cand(period="2024-03"), _cand(entity=None)]
    )
    assert entity.status == period.status == none.status == "refused"
    assert not entity.entity_period_ok and not period.entity_period_ok


def test_speculation_and_unknown_figure(ctx, tmp_path):
    deps = _book(ctx, tmp_path)
    spec, fig = _static(
        ctx,
        deps,
        [
            _cand(quote=None, evidence=None, text_template="Prices will fall."),
            _cand(quote=None, evidence=None, text_template="Up {F7}", figures=["F7"]),
        ],
    )
    assert spec.status == "speculation" and fig.status == "refused"


LEGAL_Q = "Value Added Tax on fuel is 16 per cent"


def _legal(**kw):
    base = dict(
        text_template="VAT on fuel is 16 per cent.", quote=LEGAL_Q, claim_type="legal_status",
        entity="fuel", metric="vat", period="2026", legal_stage="in_force",
        legal_date=date(2026, 7, 1),
    )  # fmt: skip
    return _cand(**{**base, **kw})


def test_legal_status_needs_stage(ctx, tmp_path):
    deps = _book(ctx, tmp_path)
    no_stage, no_date, good = _fact(
        ctx, deps, [_legal(legal_stage=None), _legal(legal_date=None), _legal()]
    )
    assert no_stage.status == no_date.status == "refused"
    assert good.status == "fact" and good.legal_stage == "in_force"


def test_legal_status_needs_tier1_even_with_corroboration(ctx, tmp_path):
    deps = _book(ctx, tmp_path, url=T3)
    deps.book.add_text(T1, PAGE, None, date(2026, 9, 15))
    press, official = _fact(ctx, deps, [_legal(), _legal(evidence="E2")])
    assert official.status == "fact"
    assert press.status == "inference" and "needs a tier 1" in " ".join(press.reasons)


def test_tier3_alone_is_inference(ctx, tmp_path):
    deps = _book(ctx, tmp_path, url=T3)
    (c,) = _fact(ctx, deps, [_cand()])
    assert c.tier == 3 and c.status == "inference"


def test_tier3_with_tier1_corroboration_is_fact(ctx, tmp_path):
    deps = _book(ctx, tmp_path, url=T3)
    deps.book.add_text(T1, PAGE, None, date(2026, 9, 15))
    press, official, press2 = _fact(ctx, deps, [_cand(), _cand(evidence="E2"), _cand()])
    assert (press.tier, official.tier) == (3, 1)
    assert press.status == official.status == press2.status == "fact"
    # a tier 1 claim that disagrees does not corroborate
    other = _cand(
        evidence="E2", quote="Value Added Tax on fuel is 16 per cent",
        text_template="Super petrol costs 16 per cent.", period="2026-09",
    )  # fmt: skip
    a, _ = _fact(ctx, deps, [_cand(), other])
    assert a.status == "inference"


def test_stale_is_inference(ctx, tmp_path):
    deps = _book(ctx, tmp_path, published=date(2026, 6, 1))
    undated = make_deps(ctx, tmp_path / "u")
    undated.book.add_text(T1, PAGE, None, None)
    (old,) = _fact(ctx, deps, [_cand()])
    (nodate,) = _fact(ctx, undated, [_cand()])
    assert old.stale and old.status == "inference"
    assert nodate.stale and nodate.status == "inference"  # an undated price is stale
    fresh = _book(ctx, tmp_path / "f")
    assert _fact(ctx, fresh, [_cand()])[0].status == "fact"


def test_sensitive_needs_tier1_and_yes(ctx, tmp_path):
    deps = _book(ctx, tmp_path, url=T3)
    deps.book.add_text(T1, PAGE, None, date(2026, 9, 15))
    c3, c1, kw = _static(
        ctx, deps, [_cand(names_person=True), _cand(names_person=True, evidence="E2"),
                    _cand(text_template="Fraud hit Nairobi at KSh 198.00 per litre.")],
    )  # fmt: skip
    assert c3.sensitive and c3.status == "refused"  # tier 3
    assert kw.sensitive and kw.status == "refused"  # keyword, tier 3
    assert c1.sensitive and c1.status == "inference"  # tier 1 passes the static step
    c1.entailment = "partial"
    (out,) = assign_status([c1], [])
    assert out.status == "refused"
    c1.entailment = "yes"
    assert assign_status([c1], [])[0].status == "fact"


def _c(cid, text, tier=1, published=None, metric="m", entity="e", period="2026-09", **kw):
    return Claim(
        id=cid, text=text, text_template=text, status="inference", quote="q", evidence_id="ev1",
        quote_grounded=True, numbers_ok=True, entity_period_ok=True, entailment="yes", tier=tier,
        metric=metric, entity=entity, period=period, published=published, **kw,
    )  # fmt: skip


def test_conflict_resolution_tier_then_newer():
    t1, t3 = _c("C1", "Rate is 8%.", 1), _c("C2", "Rate is 16%.", 3)
    (k,) = detect_conflicts([t1, t3])
    assert (k.resolution, k.kept, k.claim_ids) == ("prefer_higher_tier", "C1", ["C1", "C2"])
    assert (k.metric, k.entity, k.period, k.values) == ("m", "e", "2026-09", ["8", "16"])
    out = {c.id: c for c in assign_status([t1, t3], [k])}
    assert out["C1"].status == "fact" and out["C2"].status == "inference"
    assert out["C2"].conflicts == [k.id] and "conflict" in " ".join(out["C2"].reasons)

    old, new = (
        _c("C1", "Rate is 8%.", 1, date(2026, 1, 1)),
        _c("C2", "Rate is 9%.", 1, date(2026, 9, 1)),
    )
    (k,) = detect_conflicts([old, new])
    assert (k.resolution, k.kept) == ("prefer_newer", "C2")
    out = {c.id: c for c in assign_status([old, new], [k])}
    assert out["C2"].status == "fact" and out["C1"].status == "inference"

    undated = _c("C1", "Rate is 8%.", 1), _c("C2", "Rate is 9%.", 1, date(2026, 9, 1))
    (k,) = detect_conflicts(list(undated))
    assert (k.resolution, k.kept) == ("unresolved", None)
    assert {c.status for c in assign_status(list(undated), [k])} == {"inference"}


def test_conflict_tolerance_and_scope():
    same = [
        _c("C1", "Rate is 100."),
        _c("C2", "Rate is 100.4."),
        _c("C3", "Rate is 7.", entity="x"),
    ]
    assert detect_conflicts(same) == []  # within 0.5%; different entity
    assert detect_conflicts([_c("C1", "8%"), _c("C2", "9%", period="2026-10")]) == []
    refused = _c("C2", "Rate is 16%.")
    refused.status = "refused"
    assert detect_conflicts([_c("C1", "Rate is 8%."), refused]) == []
    assert detect_conflicts([_c("C1", "Rate is 8%."), _c("C2", "Rate is 8 percent in 2026.")]) == []


class _Prompts:
    def __init__(self, verdict="yes"):
        self.prompts: list[str] = []
        self.verdict = verdict

    def __call__(self, prompt):
        self.prompts.append(prompt)
        ids = [p.split('"')[1] for p in prompt.split("<claim id=")[1:]]
        v = self.verdict if isinstance(self.verdict, dict) else dict.fromkeys(ids, self.verdict)
        return {"verdicts": [{"claim": i, "verdict": v[i]} for i in ids if i in v]}


async def test_entailment_judge_sees_only_claim_and_quote(ctx, tmp_path):
    deps = _book(ctx, tmp_path)
    claims = _static(ctx, deps, [_cand() for _ in range(6)])
    refused = _static(ctx, deps, [_cand(entity="Mombasa")])[0]
    refused.id = "C99"
    judge = _Prompts({f"C{i}": "yes" for i in range(1, 6)} | {"C6": "no"})
    out = await entail([*claims, refused], deps.book, deps, model=function_model_returning(judge))
    assert len(judge.prompts) == 2  # batches of 5
    first, second = judge.prompts
    assert first.count("<claim id=") == 5 and second.count("<claim id=") == 1
    assert "C99" not in first + second  # refused claims are never sent
    assert claims[0].text in first and Q in first
    for leak in ("epra.go.ke", "15 September", "tier", "Value Added Tax", "zzz"):
        assert leak not in first + second
    assert [c.entailment for c in out[:6]] == ["yes"] * 5 + ["no"]
    assert out[5].status == "refused" and out[6].entailment == "not_run"
    assert claims[0].entailment == "not_run"  # inputs are not mutated


async def test_entail_figure_only_is_yes_and_failures_are_soft(ctx, tmp_path):
    deps = _book(ctx, tmp_path)
    fig = Claim(
        id="C1", text="Up 10.0%.", text_template="Up {F1}", status="inference", figure_ids=["F1"],
        numbers_ok=True, entity_period_ok=True,
    )  # fmt: skip
    (quoted,) = _static(ctx, deps, [_cand()])

    def boom(_):
        raise RuntimeError("provider down sk-secret")

    calls = _Prompts()
    out = await entail([fig], deps.book, deps, model=function_model_returning(calls))
    assert out[0].entailment == "yes" and calls.prompts == []
    out = await entail([quoted], deps.book, deps, model=function_model_returning(boom))
    assert out[0].entailment == "not_run" and "entailment not run" in out[0].reasons[-1]
    assert assign_status(out, [])[0].status == "inference"
    # a judge that skips a claim leaves it unjudged
    out = await entail([quoted], deps.book, deps, model=function_model_returning(_Prompts({})))
    assert out[0].entailment == "not_run" and "no verdict" in out[0].reasons[-1]


def test_generic_extraction_caps_at_inference(ctx, tmp_path):
    deps = _book(ctx, tmp_path)

    def pack(generic):
        ref = FigureRef(
            label="F1", figure_id="F1", series="s", entity="Nairobi", period_label="2026-09",
            generic=generic, tier=1, published=date(2026, 9, 15),
        )  # fmt: skip
        return FigurePack(refs=[ref], markdown="", comparisons_csv="")

    fbook = FigureBook()
    fbook.figures.append(
        Figure(id="F1", label="l", value=Decimal("198"), unit="KES/L", formula="value", inputs=[])
    )
    cand = _cand(quote=None, evidence=None, text_template="Super is {F1}.", figures=["F1"])
    (plain,) = _fact(ctx, deps, [cand], pack=pack(False), fbook=fbook)
    (generic,) = _fact(ctx, deps, [cand], pack=pack(True), fbook=fbook)
    assert plain.status == "fact" and plain.text == "Super is 198.00 KES/L."
    assert generic.generic and generic.status == "inference"
    assert "generic extraction" in generic.reasons


def _brief(verdict="supported", priorities=(1, 2)):
    return ResearchBrief(
        topic="t", core_question="q", framing_challenge="f",
        angles=[Angle(label="a", thesis="t", contrarian=True)],
        chart_concepts=[ChartConcept(id="c1", relationship="ranking", idea="i")],
        data_needs=[
            DataNeed(kind="series", question=f"q{p}", priority=p) for p in priorities
        ],
        verdict=verdict, verdict_reasons=["orig"],
    )  # fmt: skip


def _st(need, status="satisfied", ev=()):
    return NeedStatus(need_id=need, status=status, evidence_ids=list(ev))


def test_recheck_verdict_downgrades():
    fact = _c("C1", "Rate is 8%.")
    fact.status = "fact"
    infer = _c("C2", "Rate is 8%.")
    ok = [_st("n1", ev=["ev1"]), _st("n2")]
    assert recheck_verdict(_brief(), [fact], ok) == ("supported", ["orig"])
    v, why = recheck_verdict(_brief(), [infer], ok)  # headline is not a fact
    assert v == "reframed" and "C2" in why[-1]
    two = _brief(priorities=(1, 1, 2))
    v, why = recheck_verdict(
        two, [fact], [_st("n1", "not_found"), _st("n2", ev=["ev1"]), _st("n3")]
    )
    assert v == "reframed" and "priority-1 need not found: n1" in why[-1]
    v, why = recheck_verdict(_brief(), [fact], [_st("n1", "not_found"), _st("n2")])
    assert v == "reject" and "every priority-1 need" in why[-1]
    v, why = recheck_verdict(
        _brief("reframed"), [fact], [_st("n1", "not_found"), _st("n2", "not_found")]
    )
    assert v == "reject" and "every priority-1 need" in why[-1]
    # no claim on a priority-1 need at all: the headline cannot be a fact
    v, why = recheck_verdict(_brief(), [], [_st("n1"), _st("n2")])
    assert v == "reframed" and "no claim on a priority-1 need" in why[-1]
    # refused claims are not headline candidates; the first candidate decides
    refused = _c("C0", "Rate is 8%.")
    refused.status = "refused"
    assert recheck_verdict(_brief(), [refused, fact, infer], ok)[0] == "supported"
    assert recheck_verdict(_brief(), [infer, fact], ok)[0] == "reframed"
    # a reject verdict stays a reject; a claim on a priority-2 need is not the headline
    assert recheck_verdict(_brief("reject"), [fact], ok)[0] == "reject"
    other = _c("C3", "Rate is 8%.")
    other.evidence_id = "ev9"
    assert recheck_verdict(_brief(), [other, fact], ok)[0] == "supported"
    # figure-linked headline
    figure_claim = _c("C4", "Up 10.0%.", figure_ids=["F2"])
    figure_claim.evidence_id, figure_claim.status = None, "fact"
    st = [NeedStatus(need_id="n1", status="satisfied", figure_ids=["F2"]), _st("n2")]
    assert recheck_verdict(_brief(), [figure_claim], st)[0] == "supported"


# --- headline challenge ---------------------------------------------------------------------


def _headline():
    c = _c("C1", "VAT on fuel is 8%.")
    c.entity, c.metric, c.period = "fuel", "vat", "2026"
    return c


@pytest.mark.parametrize(
    ("out", "expect"),
    [
        ({"contradiction": False}, None),
        (
            {"contradiction": True, "url": T1, "quote": "Value Added Tax on fuel is 16 per cent"},
            "ok",
        ),
        (
            {"contradiction": True, "url": T1, "quote": "VAT on fuel is 0 per cent"},
            None,
        ),  # invented
        ({"contradiction": True, "url": "https://nowhere.go.ke/x", "quote": "x"}, None),  # unseen
        (
            {"contradiction": True, "url": T3, "quote": "Value Added Tax on fuel is 16 per cent"},
            None,
        ),
        ({"contradiction": True, "url": T1}, None),
    ],
)
async def test_challenge_note_is_built_from_a_grounded_tier1_quote(ctx, tmp_path, out, expect):
    deps = _book(ctx, tmp_path)
    deps.book.add_text(T3, PAGE, None, None)
    note = await challenge(_headline(), _brief(), deps, model=function_model_returning(out))
    if expect is None:
        assert note is None
    else:
        assert note is not None and "epra.go.ke" in note and "16 per cent" in note


async def test_challenge_fails_soft(ctx, tmp_path):
    deps = _book(ctx, tmp_path)

    def boom(_):
        raise RuntimeError("down")

    assert (
        await challenge(_headline(), _brief(), deps, model=function_model_returning(boom)) is None
    )
    assert any("challenge" in line for line in deps.log)


# --- golden: fuel VAT with a prompt-injection page ---------------------------------------------

GOLDEN = __import__("pathlib").Path(__file__).parent / "fixtures" / "golden" / "fuel_vat"


async def test_golden_fuel_vat(ctx, tmp_path):
    import json

    from kenya_data_engine.research.claims import write_claims

    deps = make_deps(ctx, tmp_path)
    for src in json.loads((GOLDEN / "sources.json").read_text()):
        ev = deps.book.add_text(
            src["url"],
            (GOLDEN / src["file"]).read_text(),
            None,
            date.fromisoformat(src["published"]),
        )
        assert (ev.label, ev.tier) == (src["label"], src["tier"])
    cands = json.loads((GOLDEN / "candidates.json").read_text())
    judge = json.loads((GOLDEN / "judge.json").read_text())
    expected = json.loads((GOLDEN / "expected.json").read_text())
    pack = FigurePack(refs=[], markdown="", comparisons_csv="")
    seen: list[str] = []

    def writer(prompt):
        seen.append(prompt)
        return ClaimsOutput(claims=[CandidateClaim(**c) for c in cands])

    brief = _brief()
    written = await write_claims(
        brief, deps.book, pack, deps, model=function_model_returning(writer)
    )
    assert "ignore previous instructions" in seen[0]  # the page reaches the writer as data
    assert 'untrusted="true"' in seen[0]

    # a fooled judge: it says "yes" to the injected claim; only the verdicts file differs per id
    claims = _static(ctx, deps, written)
    judged = await entail(claims, deps.book, deps, model=function_model_returning(_Prompts(judge)))
    final = assign_status(judged, detect_conflicts(judged))
    got = {c.id: {"status": c.status, "legal_stage": c.legal_stage} for c in final}
    assert got == expected
    by_id = {c.id: c for c in final}
    # the Bill claim is a fact at the bill stage; "cut to 8% is law" never is
    assert by_id["C1"].status == "fact" and by_id["C1"].legal_stage == "bill"
    assert by_id["C3"].status != "fact"
    # the injected page yields no fact, and neither does anything it contradicts
    assert by_id["C4"].evidence_id == "ev4" and by_id["C4"].status != "fact"
    assert by_id["C4"].tier == 4 and by_id["C5"].status == "refused"
    # the tier 1 EPRA rate wins the conflict over the press and the injected page
    (k,) = detect_conflicts(judged)
    assert (k.resolution, k.kept) == ("prefer_higher_tier", "C2")
    assert by_id["C2"].status == "fact" and by_id["C2"].conflicts == [k.id]
