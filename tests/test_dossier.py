import csv
import json
from datetime import date

from test_orchestrator import TOPIC, WB_BODY, WB_URL, brief_dict, models

from kenya_data_engine.research.dossier import (
    DossierDoc,
    dossier_dir,
    slugify,
    write_dossier,
)
from kenya_data_engine.research.memory import TopicMemory
from kenya_data_engine.research.models import Claim, NeedStatus, ResearchBrief, Scorecard
from kenya_data_engine.research.orchestrator import ResearchOutcome, load_book, research

TODAY = date(2026, 10, 9)


def test_slugify_unicode_and_symbols():
    assert slugify("Bei ya mafuta: Je, VAT ni 8%? 🚗") == "bei-ya-mafuta-je-vat-ni-8"
    assert slugify("Café Ünïcode — Nairobi") == "cafe-unicode-nairobi"
    assert len(slugify("word " * 40)) <= 50 and not slugify("word " * 40).endswith("-")


def test_slugify_empty_is_topic():
    assert slugify("") == "topic" and slugify("🚗 !!! ???") == "topic"


def test_dossier_dir_increments(tmp_path):
    first = dossier_dir(tmp_path, TODAY, "fuel")
    assert first == tmp_path / "2026-10-09" / "01-fuel"
    first.mkdir(parents=True)
    (tmp_path / "2026-10-09" / "05-other").mkdir()
    assert dossier_dir(tmp_path, TODAY, "fuel") == tmp_path / "2026-10-09" / "06-fuel"
    assert dossier_dir(tmp_path, date(2026, 10, 10), "x") == tmp_path / "2026-10-10" / "01-x"


async def _wb_outcome(ctx, respx_mock):
    respx_mock.get(WB_URL).respond(json=WB_BODY)
    m, _ = models()
    return await research(TOPIC, ctx, models=m)


async def test_write_dossier_files_and_schema(ctx, respx_mock):
    outcome = await _wb_outcome(ctx, respx_mock)
    book = load_book(ctx)
    root = write_dossier(outcome, ctx, book, today=TODAY)
    assert root == ctx.home.briefs_dir / "2026-10-09" / "01-kenya-inflation"
    for rel in (
        "README.md",
        "brief.md",
        "stats.md",
        "sources.json",
        "gaps.md",
        "dossier.json",
        "research/claims.json",
        "research/comparisons.csv",
        "research/verification.md",
    ):
        assert (root / rel).is_file(), rel
    (csv_path,) = (root / "data").glob("n1-*.csv")
    rows = list(csv.DictReader(csv_path.open()))
    assert list(rows[0]) == ["period", "entity", "metric", "value", "unit", "source_url"]
    assert {r["period"] for r in rows} == {"2024", "2025"} and rows[0]["entity"] == "Kenya"

    doc = DossierDoc.model_validate_json((root / "dossier.json").read_text())
    assert doc.schema_version == 1 and doc.run_id == outcome.run_id
    assert len(doc.config_hash) == 64 and "planner" in doc.prompt_hashes
    assert doc.model and doc.path == str(root) and "README.md" in doc.files
    assert json.loads((root / "dossier.json").read_text())["schema_version"] == 1

    sources = json.loads((root / "sources.json").read_text())
    (src,) = sources
    assert src["tier"] == 1 and src["kind"] == "series" and src["vintage"] == 1
    assert src["attribution"].startswith("Source: ") and len(src["sha256"]) == 64

    readme = (root / "README.md").read_text()
    assert "15-minute check" in readme and "Verdict: supported" in readme
    assert "How high is inflation?" in readme and "| c1 |" in readme and "ready" in readme
    assert csv_path.name in readme and "api.worldbank.org" in readme
    claims = json.loads((root / "research/claims.json").read_text())
    assert len(claims["facts"]) == 1 and claims["refused"] == []

    # topic memory and blob refs
    assert TopicMemory(ctx.home.db_path).last_covered("Kenya inflation", TODAY) == 0
    from kenya_data_engine.data.store import BlobStore

    assert src["sha256"] in BlobStore.referenced(ctx.home.db_path)
    # a second dossier for the same day gets the next number
    assert write_dossier(outcome, ctx, book, today=TODAY).name == "02-kenya-inflation"
    print(readme)


def _brief(**kw):
    return ResearchBrief.model_validate(brief_dict(**kw))


def _score(**kw):
    return Scorecard(
        preset="standard", usd_spent=0.01, usd_cap=0.15, credits_spent=1, credits_cap=25,
        seconds=3.0, **kw,
    )  # fmt: skip


async def test_rejected_dossier_is_short(ctx):
    brief = _brief(needs=[], verdict="reject", verdict_reasons=["no numbers exist for this claim"])
    outcome = ResearchOutcome(
        run_id=ctx.run.run_id, brief=brief, statuses=[], claims=[], conflicts=[],
        verdict="reject", verdict_reasons=["no numbers exist for this claim"],
        scorecard=_score(), gaps=[], challenge_note=None, figures=None,
        stopped_because="rejected by the planner",
    )  # fmt: skip
    root = write_dossier(outcome, ctx, load_book(ctx), today=TODAY)
    found = sorted(p.name for p in root.rglob("*") if p.is_file())
    assert found == ["README.md", "brief.md", "dossier.json"]
    readme = (root / "README.md").read_text()
    assert "Verdict: reject" in readme and "no numbers exist for this claim" in readme
    assert "15-minute check" not in readme


async def test_text_fact_links_page_and_secrets_are_redacted(ctx):
    quote = "Super petrol retails at KSh 198.00 per litre in Nairobi"
    book = load_book(ctx)
    ev = book.add_text(
        "https://www.epra.go.ke/prices.pdf",
        f"[page 1]\nIntro.\n\n[page 4]\n{quote}. Token fake-deepseek here.",
        "Prices",
        date(2026, 9, 15),
    )
    claim = Claim(
        id="C1", text="Super petrol costs KSh 198.00 per litre.", text_template="x",
        status="fact", evidence_id=ev.id, quote=quote, quote_grounded=True, tier=1,
    )  # fmt: skip
    refused = claim.model_copy(update={"id": "C2", "status": "refused", "reasons": ["bad"]})
    outcome = ResearchOutcome(
        run_id=ctx.run.run_id, brief=_brief(), claims=[claim, refused], conflicts=[],
        statuses=[NeedStatus(need_id="n1", status="not_found", notes=["tried it: 404"])],
        verdict="reframed", verdict_reasons=["priority-1 need not found: n1"],
        scorecard=_score(facts=1, refused=1), gaps=["n1 not_found: Kenya CPI inflation"],
        challenge_note="Tier 1 source x.go.ke may contradict: fake-deepseek",
        figures=None, stopped_because="max rounds (3) reached",
    )  # fmt: skip
    root = write_dossier(outcome, ctx, book, today=TODAY)
    readme = (root / "README.md").read_text()
    assert "https://www.epra.go.ke/prices.pdf" in readme and "page 4" in readme
    assert "not possible" in readme and "n1 not_found" in readme
    assert "1 claims were refused" in readme
    for p in root.rglob("*"):
        if p.is_file():
            assert "fake-deepseek" not in p.read_text(), p
    assert not list((root / "data").glob("*"))  # no series data, no CSV
