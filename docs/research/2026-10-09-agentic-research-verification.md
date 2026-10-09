# Agentic research and verification: patterns for Plan 2

_2026-10-09. Input to Plan 2 (Planner, Data Scout, verification). Practical for a founder-scale tool at
cents per dossier. Evidence quality is flagged: [P] primary doc, [S] secondary summary, [U] unverified
here. The cloud session could not fetch `ai.pydantic.dev` or `pydantic.dev` pages directly (DNS), so the
Pydantic AI notes come from search excerpts and must be re-checked against the installed 2.54 API
(`uv run python -c "import pydantic_ai; help(pydantic_ai.usage.UsageLimits)"`) before coding._

## 1. What the deep-research systems teach

| Source | Pattern | Take for us |
|---|---|---|
| [Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) [P] | Lead plans, parallel subagents with own context, then a CitationAgent attributes claims. Effort scaling written into the prompt: simple fact-finding 1 agent / ~3-10 tool calls; comparisons 2-4 agents / 10-15 calls each; complex 10+. Token use explained ~80% of BrowseComp variance; multi-agent costs ~15x chat tokens. | Our work is "comparison-sized": 2-4 scouts, hard cap per scout. Put the effort rule in the Planner prompt and enforce it in code. |
| Same post, failure modes | Spawning 50 agents for easy queries; endless search for sources that do not exist; continuing after enough results; vague briefs causing duplicate work; agents favouring SEO farms over primary sources. | Each scout gets a typed brief (objective, output schema, allowed tools, stop rule). A "not found" result is a valid output. Source hierarchy in code (section 5). |
| Same post, evals | ~20 realistic queries are enough early; LLM judge with rubric (accuracy, citation accuracy, completeness, source quality, tool efficiency); judge outcomes not paths. | Our fuel dossier is the first golden case. Add 5-10 more, run offline with recorded fixtures. |
| [LangChain open_deep_research](https://blog.langchain.com/open-deep-research) [S] | Scope, then supervisor with `ConductResearch` / `ResearchComplete` tools and `think_tool`, sub-agents in parallel, capped by `max_concurrent_research_units`; final report step. Defaults for iteration caps not confirmed [U]. | Copy the shape: explicit "done" tool/field, a concurrency cap, a reflect step. We do not need a supervisor loop: code fans out because requirements are enumerated by the Planner. |
| GPT-Researcher / STORM / Co-STORM, OpenAI and Google deep research | Not re-fetched in this pass; widely described as plan, parallel retrieval, then synthesis with citations. STORM adds perspective-driven question asking (maps to our contrarian angle). | No change to design; "perspectives" justify the contrarian angle. |

**"Enough evidence" decisions, practical version.** Do not ask the model "is this enough?" as the primary
stop. Use code-checkable completeness: every `DataRequirement` the Planner listed is either `satisfied`
(a parsed table passing checks), `partial`, or `not_found` after N attempts. Stop when all are resolved or
the budget is hit. The model only proposes the next query; code decides whether to continue.

## 2. Recommended architecture and budgets

```
topic ──► Planner (1 agent, <=8 tool calls, <=1 retry on validator)
            └─ ResearchBrief: core_question, angles[incl. contrarian], framing_challenge,
               chart_concepts[], data_requirements[<=6], story_verdict
          verdict == reject  ──► stop, write brief.md with reasons (cheap exit)
          ▼ code fan-out (asyncio.gather, Semaphore(3))
        Scout x R (one per requirement, <=10 tool calls, <=6 model requests each)
            └─ DataSourceSpec(s) {url, format, locator, why, tier, vintage_hint}
          ▼ code: fetch + extract (CSV/XLSX/HTML/PDF), checks, pandas stats
        ExtractionResult per requirement  (numbers only come from here)
          ▼
        Claim writer (1 agent, no tools): proposes claims from evidence packets
          ▼ code verification pipeline (section 3)
        claims.json + verification.md + gaps.md
```

Budgets (cents-scale; DeepSeek Flash input is cheap, so tool calls and fetch latency matter more than tokens):

| Level | Cap | Enforced by |
|---|---|---|
| Whole dossier | $0.10 hard, $0.03 target; 60 tool calls; 5 min wall | `Tracer.check_budget` plus a run-level counter |
| Planner | 8 tool calls, 8 requests, 1 output retry | `UsageLimits` |
| Scout | 10 tool calls, 6 requests, max 3 distinct domains fetched | `UsageLimits` plus deps counter |
| Claim writer | 0 tools, 2 requests | `UsageLimits` |
| Fan-out | `Semaphore(3)`, max 6 requirements | code |
| Fetched text into prompts | 6k tokens per page excerpt, truncate with offset notes | code |

Rule: each scout failure is recorded in `gaps.md` and the dossier ships with what it has (fail soft, report
loudly). A dossier with a `not_found` requirement is allowed; a claim with no quote is not.

Do not use self-consistency voting for scouting (costly, little gain). Use it only for the one
judgement that is cheap and high leverage: story verdict (3 samples of a no-tool call, majority wins, disagreement
is logged and downgrades `supported` to `reframed`). Optional.

## 3. Verification pipeline (code vs LLM)

Based on the SAFE / FActScore decomposition idea ([SAFE](https://arxiv.org/abs/2403.18802): split into atomic facts,
make each self-contained, search, then rate support; [FActScore](https://arxiv.org/abs/2305.14251): share of atomic facts supported by a
reliable source), cut down because our evidence is already retrieved and our numbers are computed.

| # | Step | Who | Notes |
|---|---|---|---|
| 1 | Claim extraction/decomposition into atomic, self-contained claims, each with a `quote` and `evidence_id` | LLM (Flash, no tools) | Narrow task: "given these evidence snippets, list atomic claims; copy the supporting sentence verbatim." |
| 2 | Quote grounding | **code** (`quote_in_text`) | Exact match against the stored page text. Fail means claim dropped (or one retry via `ModelRetry` telling the model the quote was not found). |
| 3 | Number check | **code** | Every number in the claim text must appear as a token in the quote (reuse `_has_number`) or be a registered computed figure (`derived_from`). Computed figures come from pandas, never from the LLM. |
| 4 | Entity/period check | **code** | Claim's place, fuel type, period, unit must match the table row/column provenance (e.g. Nairobi, Super, Jul-Aug 2026). |
| 5 | Source tiering and vintage | **code** | Tier from domain allowlist (section 5); vintage = publication date and reference period parsed from page/metadata/table header; `stale` if older than per-claim-type limit (e.g. prices 45 days, CPI 2 months, annual stats 18 months). |
| 6 | Entailment judgement: does the quote actually support the claim wording (not just contain the numbers)? | LLM (Flash, one claim per call, yes/partial/no plus reason) | Needed because exact quote plus number match can still support a different claim (direction, scope, "proposed" vs "enacted"). Run only on claims that passed 2-5. Small NLI or small LLM verifiers are viable and cheap ([ICCS 2025](https://www.iccs-meeting.org/archive/iccs2025/papers/159110235.pdf) [S]); known weakness is false entailment on partial evidence, so treat `partial` as `inference`. |
| 7 | Conflict detection | code first, LLM second | Group claims by `(metric, entity, period)`; if values differ beyond tolerance, create a `conflict` record; LLM only drafts a one-line explanation (revision? definition? different tier?). Resolution follows source hierarchy, never LLM opinion. |
| 8 | Status assignment | **code** | `fact` = grounded + entailed + tier 1-2 + not stale; `inference` = grounded but derived, partial, tier 3, or stale; `speculation` = no direct quote (kept only if flagged, excluded from chart concepts). Unsupported = removed and listed in `verification.md` ("refuse when unsupported"). |
| 9 | Adversarial pass on the headline claim only | LLM, optional | "Find one reason this is wrong" using the framing challenge from the brief; search once for primary-source contradiction. Mirrors Anthropic's lesson that agents miss gaps without a reflect step. |

Steps 2-5, 7 (detection), 8 are free. Steps 1, 6 are about 1 + N cheap calls (N ~ 10-25 claims, ~300 tokens each).

## 4. Claim schema proposal

```python
class Evidence(BaseModel):
    id: str                      # "E3", short labels as in synth (stops id mis-copying)
    url: str
    tier: Literal[1, 2, 3, 4]
    publisher: str
    published: date | None
    reference_period: str | None  # "2026-09-15 to 2026-10-14"
    retrieved_at: datetime
    content_hash: str            # of stored text, so quote check is reproducible
    text_path: str               # runs/<id>/evidence/E3.txt

class Claim(BaseModel):
    id: str                      # "C7"
    text: str                    # atomic, self-contained, no pronouns
    status: Literal["fact", "inference", "speculation"]
    evidence_id: str
    quote: str                   # verbatim from evidence text
    quote_grounded: bool         # set by code only
    numbers_checked: bool        # set by code only
    entailment: Literal["yes", "partial", "no", "not_run"]
    claim_type: Literal["price", "rate", "legal_status", "statistic", "event", "forecast", "other"]
    entity: str | None; metric: str | None; period: str | None; unit: str | None
    value: float | None          # parsed by code from the table, never from LLM
    derived_from: list[str] = [] # claim ids; computation in stats.md
    vintage: Vintage             # published, reference_period, age_days, stale: bool, revised: bool|None
    conflicts: list[Conflict] = []   # {other_claim_id, delta, resolution: "prefer_higher_tier"|"prefer_newer"|"unresolved", note}
    caveats: list[str] = []
    refused_reason: str | None   # only in the refused list
```

`claims.json` = `{claims: [...], refused: [...], conflicts: [...], evidence: [...]}`. Editorial agent (Plan 4)
may only cite `status == "fact"` claims as unqualified statements; `inference` needs hedged wording;
`speculation` is not usable on a chart.

## 5. Source hierarchy and vintage rules

Tiers, assigned by code from a domain/type allowlist in config (not the LLM):

1. **Primary/official originator**: EPRA schedules, CBK data and MPC statements, KNBS releases, National Treasury, Kenya Gazette, Parliament (Hansard, Bills), Kenya Law, CMA/NSE filings, World Bank/IMF data APIs.
2. **Official secondary**: Controller of Budget, KRA notices, Auditor-General, regulator circulars that reproduce primary text.
3. **Reputable press** quoting primary documents: Business Daily, The Star, Nation, Reuters, Bloomberg (use only as pointer unless no primary exists).
4. **Other** (blogs, aggregators, social posts): never sufficient for `fact`; may be a lead for a scout.

Rules:
- A `fact` needs tier 1, or tier 2/3 plus a tier 1 corroboration; a tier 3 claim alone is `inference`.
- Higher tier wins a conflict; same tier, newer reference period wins; same period and tier stays `unresolved` and is shown in the dossier, not silently picked.
- Legal/policy claims ("VAT cut is law") need the **stage**: proposed, Bill, passed, assented, gazetted, effective date. Make `legal_status` a required field for that claim type. This is exactly the fuel dossier VAT trap.
- Vintage: always store both publication date and reference period; compare like with like (same town, fuel, month). Flag KNBS/CBK revisions: if a figure is older than the latest release of the series, mark `revised: unknown` and prefer re-fetching the series page. Never quote a figure with an age beyond its type limit as "current".
- Anthropic observed agents preferring SEO content over authoritative sources; tier ranking in code fixes this ([post](https://www.anthropic.com/engineering/multi-agent-research-system)).

## 6. Newsroom methods worth borrowing

- [Africa Check](https://africacheck.org/how-we-fact-check): fixed, repeatable steps per report; claim selection screens for importance and fact-versus-opinion; IFCN principles of transparency. Borrow: `verification.md` as a visible method log, fixed steps, explicit rating vocabulary (our fact/inference/speculation).
- [Full Fact](https://fullfact.org/blog/2021/apr/ai-google-100000--claims-day/): a "claim" is the checkable part of a sentence; automation detects and matches, humans judge nuance; the Africa Check collaboration describes claim detection, claim matching to prior fact-checks, and robot-checking ([chapter](https://emerald.com/insight/content/doi/10.1108/978-1-80455-135-620231001)). Borrow: **claim matching to prior checks** is a cheap feature, so have the Planner search Africa Check / PesaCheck for existing verdicts on the topic before building a story, and cite them as tier 3 leads. Also: only check-worthy claims go through the expensive path (here: headline claims get the adversarial pass).
- ClaimBuster is mainly a check-worthiness baseline [S]; our Radar rubric already plays that role.

## 7. Pydantic AI implementation notes

Pinned: `pydantic-ai-slim[openai]>=2.54,<3`. Verify signatures against the installed version.

- **Delegation and usage**: the official multi-agent guide ([Multi-Agent Patterns](https://pydantic.dev/docs/ai/guides/multi-agent-applications/)) describes agent delegation as one agent calling another inside a tool, and says to pass `usage=ctx.usage` so the delegate's usage counts toward the parent. For us, code fan-out is simpler than tool-based delegation: the Planner returns requirements, code runs scouts. If a scout is called from a tool, pass `ctx.usage`. [P via search excerpt]
- **Limits**: `UsageLimits(request_limit=..., tool_calls_limit=..., total_tokens_limit=...)`, passed to `agent.run(..., usage_limits=...)`. Hitting a limit raises `UsageLimitExceeded`: catch it, record the partial state as a gap, do not crash the dossier. The current `run_agent` creates its own `RunUsage` and takes no limits; Plan 2 task 1 must add `usage_limits`, `deps`, and per-agent budgets. [P: limits named in the guide; `tool_calls_limit` confirm in installed version]
- **Deps and RunContext**: Pydantic AI's `RunContext[Deps]` is distinct from our `kenya_data_engine.context.RunContext` (name clash, alias on import). Put our engine context, a per-agent `BudgetCounter`, the evidence store, and a `seen_urls` set in a `ScoutDeps` dataclass; tools read `ctx.deps`. Type the agent `Agent[ScoutDeps, ScoutOutput]`, which means `run_agent` must become generic in deps.
- **Output validators and retries**: `@agent.output_validator` with `raise ModelRetry("...specific fix...")`; output retries are a separate budget set with `retries={'output': N}` / `ToolOutput(max_retries=N)` per the [retries page](https://pydantic.dev/docs/ai/core-concepts/retries/index.md) (older name `output_retries`; default 1). Use validators only for cheap code checks the model can fix: quote not found in evidence, requirement id unknown, `reject` verdict without reasons, contrarian angle missing. Keep messages concrete ("quote for C3 not found in E2; copy a sentence exactly"). Each retry is a paid request: cap at 1-2.
- **Structured output with DeepSeek**: keep thinking disabled and the default tool-output mode (forced `tool_choice`), as in `llm.py`. Do not switch to native/JSON-schema mode without a live test. Mixed use of function tools and output tool works, but live-test the Planner (tools plus final output) since the earlier 400 came from forced tool choice.
- **Parallel**: `asyncio.gather` with a `Semaphore`; use `return_exceptions=True` so one scout failing is recorded, not fatal. Fetch with trafilatura via `asyncio.to_thread` (deferred minor in STATUS). Capture filenames: sanitise `name`/`stage` now since scouts add many calls.
- **Testing**: `FunctionModel`/`TestModel` for agents; respx for HTTP; a recorded-fixture golden test for the fuel dossier. Test validators directly with crafted outputs.
- **Durable/graph options**: [pydantic-graph](https://pydantic.dev/docs/ai) and Temporal/DBOS durable execution exist; skip. Our stage artifacts in `runs/<id>/` already give resume. Revisit only if scouts get long-running. A newer harness `SubAgents` capability (single `delegate_task` tool, per-subagent budgets, API "may change") exists [S]; skip for stability.

## 8. Cheap-model tactics

- One narrow job per call: extract claims, or judge one claim, never both.
- Give short labels (E1.., C1..) and map back in code (already proven in synth).
- Pass exact evidence excerpts, not whole pages; include the table row text for numeric claims.
- Make the model copy, not compose: quotes, locators, URLs chosen from search results returned by the tool (validate URL is in `seen_urls`), never invented.
- Flash is weak at long-horizon tool use: keep scouts short, give a stop rule ("if two searches return no tier 1-2 source, return `not_found`"), and prefer deterministic site-specific fetchers (EPRA, CBK, KNBS adapters) over open web search when the catalog knows the source.
- Put the effort-scaling rule in the prompt AND the budget in code; the model will not self-limit ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- Judge your own pipeline with a small rubric eval (accuracy, citation accuracy, source quality, tool efficiency); keep a human read of every dossier until the golden set passes.

## 9. Pitfalls

1. Exact quote plus matching number does not prove the claim: "proposed" vs "passed", "up" vs "down", wrong town or month. Hence steps 4 and 6.
2. Search results are leads, not evidence: always fetch and store the page, then ground against the stored text, not the snippet.
3. Computed figures: have the claim writer reference `derived_from` ids and let pandas produce values; reject any numeral in claim text not in a quote or a registered computation.
4. Stale vintage disguised as current: a page "last updated" today can contain old figures; parse the reference period from the table.
5. Paywalled/blocked Kenyan sites fail on the cloud; tests must be fixture-based, live verification on the laptop.
6. Retries multiply cost silently: count validator retries in the budget.
7. Runaway fan-out and endless search for non-existent data: hard caps plus `not_found` as a legitimate output.
8. LLM judges agreeing with themselves: the entailment judge sees only quote and claim, not the writer's reasoning.
9. Don't let the Planner's `supported` verdict bind later stages: if data requirements come back `not_found`, code downgrades the verdict to `reframed` or `reject`.
10. Secrets: scouts fetch arbitrary pages and may echo URLs with tokens; pass all captures and evidence files through `Tracer.redact`.
