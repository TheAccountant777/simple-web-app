# Plan 2 design: research, data and verified dossiers

_Status: draft for user review · 2026-10-09 · extends the approved engine spec
(`2026-10-09-kenya-data-engine-design.md` §§4.3–4.6)._

Inputs: `docs/STATUS.md` §7, `docs/research/2026-10-09-world-class-data-journalism.md` §0 and §7,
`docs/research/2026-10-09-kenya-data-catalog.md`, `docs/research/2026-10-09-table-extraction.md`,
`docs/research/2026-10-09-agentic-research-verification.md`.

---

## 0. Decisions so far (from brainstorming)

| Question | Decision |
|---|---|
| How a research run starts | **On command, one topic at a time.** From the TUI (`r` on a topic) or `engine research <topic \| "free text">`. No automatic research of the top N. |
| Budget per dossier | **Standard: $0.15 LLM, 25 searches, 5 minutes.** `lean` and `deep` presets exist. The limits are a ceiling to **use well**: the engineering goal is the most verified evidence per cent and per search, not the lowest spend. |
| Human checkpoint | **Straight through.** A `reject` verdict ends the dossier early and cheaply. `--plan-only` stops after the brief; `--force` continues past a reject. |
| Architecture | **Code orchestrates; agents are narrow workers.** No single autonomous agent, no graph framework yet. |

## 1. Goal, scope and success

**Goal.** Turn one topic into a **dossier**: a reader question, chart-ready data with provenance for
every number, and a verified claim set (fact, inference, speculation, refused). The editorial agent
(Plan 4) writes from the dossier alone, with no web access.

**In scope:** research orchestration, Planner, Scouts, budget ledger, data registry and adapters,
extraction and checks, a local data store, stats, claims and verification, the dossier, memory,
CLI, Engine Room views, an eval scorecard.

**Out of scope:** writing copy, charts and images (Plans 4 and 5), automatic research of the top N,
OCR by default, Docling by default, scheduling.

**Success criteria.**
1. **Never wrong.** Every number in a dossier is traceable to a file hash plus a page or cell, or to
   a formula over such numbers. Every fact passes quote grounding, number checks, entity and period
   checks, tier rules and entailment.
2. **Uses the budget well.** Known series cost no searches. Verified sources are remembered. Budget
   moves to open gaps. Efficiency is measured in every dossier.
3. **Passes the acceptance scenarios** in §12 on the user's laptop.
4. **Fails soft, reports loudly.** A partial dossier with an honest `gaps.md` beats a crash or a guess.

## 2. Flow

```
engine research <topic>
  │
  ├─ resolve topic: "3" (rank 3 of latest run) · "<run-id>:<topic-id>" · "free text"
  │
  ▼
Planner (LLM + tools)       free first: registry, source memory, topic memory
  → ResearchBrief           then ≤ 6 searches / ≤ 8 tool calls
  │
  ├─ verdict == reject (and no --force) → write a short dossier ("why rejected") → done
  ├─ --plan-only → write brief → done
  ▼
Rounds (code)               round 1: one Scout per need, Semaphore(3)
  │   Scout (LLM + tools) → DataSourceSpec[]       (registry/memory hit = no LLM, no search)
  │   Code: fetch → extract → check → store → Evidence
  │   Gap check (code): need → satisfied | partial | not_found
  │   Reallocate unspent budget to open needs → round 2 → round 3
  │   Stop: all satisfied · or a round adds nothing new · or the soft deadline/budget reserve hits
  ▼
Stats (code, pandas)        computed figures F1..Fn, comparisons
  ▼
Claim writer (LLM, no tools) → candidate claims over E/D/F labels
  ▼
Verification (code + 1 cheap LLM step) → statuses, conflicts, refusals
  ▼
Headline challenge (optional, reserve budget) → verdict re-check
  ▼
Dossier writer (code) → briefs/<date>/NN-<slug>/ · memory updated · scorecard recorded
```

Every step writes a checkpoint to `runs/<id>/research/` so `engine research --resume <run-id>`
continues from the last finished step, as `engine run --resume` does.

## 3. Components

New package `src/kenya_data_engine/research/`, plus `data/` for the data layer. Each unit has one
job, a typed interface, and is testable alone.

| Module | Job | LLM |
|---|---|---|
| `research/orchestrator.py` | Topic resolution, steps, rounds, gap check, reallocation, stop rule, deadlines | no |
| `research/budget.py` | Ledger: dollars, search credits, seconds; reservations; protected reserves | no |
| `research/planner.py` | Topic → `ResearchBrief` | yes |
| `research/scout.py` | One `DataNeed` → `DataSourceSpec[]` | yes |
| `research/claims.py` | Evidence packets → candidate claims | yes |
| `research/verify/` | The verification steps, statuses, conflicts | step 6 and 9 only |
| `research/dossier.py` | Writes the dossier folder and `dossier.json` | no |
| `research/memory.py` | Source memory, topic memory, novelty | no |
| `research/eval.py` | Scorecard and the eval scenario runner | no |
| `data/registry.py` | Named series and their adapters | no |
| `data/adapters/` | `worldbank`, `imf_sdmx`, `kenyalaw`, `epra`, `cbk`, `knbs`, listing crawler | no |
| `data/extract/` | Tiered extraction: CSV/XLSX → HTML table → pdfplumber (→ Docling, optional extra) | no |
| `data/checks.py` | Table checks (§5.4) | no |
| `data/store.py` | Local series store with vintages; content-addressed blob store | no |
| `data/stats.py` | Changes, like-for-like, YoY/MoM, real terms; formula log | no |
| `tools/urlpolicy.py` | URL safety policy for every fetch (§9.1) | no |
| `tools/numbers.py` | Number parsing with units and scale words (§6.3) | no |

**Prerequisite upgrade, `llm.run_agent`** (STATUS item I4): it takes `deps` (tools read
`RunContext[ResearchDeps]`), `usage_limits` (Pydantic AI `UsageLimits`: `request_limit`,
`tool_calls_limit`), an agent name for the ledger, and a `cancellation_token`. The ledger is checked
**before every model request and every search**, not once per run. Cost uses `cache_read_tokens` at
the cache-hit price from config, so the ledger is accurate when DeepSeek's prompt cache hits.

**Early spike, task 0.** Confirm on the user's laptop that DeepSeek flash with thinking disabled runs a
**tool-using** agent that also returns structured output (function tools plus the output tool in one
run). So far only tool-less structured agents ran live. `engine doctor --agents` runs a one-tool,
one-output smoke test; if it fails, the fallback is a two-phase agent (tool phase in plain text,
then a tool-less structured call).

## 4. Models (Pydantic, strict)

Short labels are used in every prompt (N1.., E1.., D1.., F1.., C1..) and mapped back to ids in
code, as S1..Sn are today.

```python
class DataNeed(BaseModel):          # N-labels
    kind: Literal["series", "fact"] # series: numbers over time; fact: a status or event to establish
    question: str                   # "Nairobi Super pump price, each EPRA cycle, last 12 cycles"
    metric: str | None; entities: list[str]; unit: str | None
    frequency: Literal["daily","weekly","monthly","quarterly","annual","cycle","none"]
    period_start: date | None; period_end: date | None
    min_points: int = 1             # coverage target the gap check uses
    priority: Literal[1, 2, 3]      # 1 = the story fails without it
    series_hint: str | None         # registry key if the Planner recognised one
    chart_concepts: list[str]       # which chart concepts depend on it

class Angle(BaseModel):
    label: str; thesis: str; contrarian: bool

class Locator(BaseModel):           # where in a file the table is; code extracts
    pages: list[int] = []; table_index: int | None; sheet: str | None
    css: str | None; columns: dict[str, str] = {}   # source header → metric/entity/period

class ChartConcept(BaseModel):
    id: str; relationship: Literal["change_over_time","ranking","part_to_whole","deviation",
        "correlation","distribution","magnitude","spatial","flow"]   # FT Visual Vocabulary
    idea: str; needs: list[str]

class ResearchBrief(BaseModel):
    topic: str; core_question: str          # phrased as the reader would ask it
    angles: list[Angle]                     # 2–3, at least one contrarian
    framing_challenge: str                  # "what would make this story wrong?"
    chart_concepts: list[ChartConcept]      # 1–4
    data_needs: list[DataNeed]              # 1–6
    verdict: Literal["supported","reframed","reject"]; verdict_reasons: list[str]
    reframe: str | None

class DataSourceSpec(BaseModel):            # what a Scout returns; code acts on it
    need: str; via: Literal["registry","file","html_table","page_text"]
    registry_key: str | None; url: str | None
    locator: Locator | None                 # page(s), table index, sheet, css, column mapping
    publisher: str; why: str; expected_period: str | None
```

Evidence has two kinds:
- **Text evidence (E):** stored, de-hyphenated page or PDF text with URL, blob hash, tier and vintage.
  Claims cite it by quote.
- **Data evidence (D):** rows in the series store with cell-level provenance. Claims cite data
  points by reference, not by quote.

Computed figures (F) come from `data/stats.py` with their formula and inputs.

`Claim`, `Conflict` and `Vintage` follow research doc §4, with these changes:
- `text_template` uses placeholders (`{F2}`, `{D7}`) that **code renders** into `text`. Literal
  numbers in the template are allowed only when they are in the claim's quote (§6.3).
- `legal_stage` is required when `claim_type == "legal_status"`: proposed, bill, passed, assented,
  gazetted or in_force, with a date.
- `sensitive: bool` is set by code when a claim names a person or alleges wrongdoing (§6.5).

## 5. Data layer

### 5.1 Two ways in
1. **Registry series** (strong path). Named keys such as `epra.pump_prices`, `cbk.cbr`,
   `cbk.tbill_auctions`, `cbk.fx_rates`, `knbs.cpi`, `wb:<indicator>`, `imf:<sdmx key>`,
   `kenyalaw.gazette`. Each adapter discovers files (crawl the listing page or call the API; PDF
   URLs are **never templated**, because CBK, CMA and Treasury filenames carry unpredictable ids),
   extracts, and returns tidy rows (`period, entity, metric, value, unit`) with provenance. It
   declares its schema, ranges and invariants for the checks. A need that matches a registry key
   costs no LLM call and no search.
2. **Discovered sources** (open path). The Scout gives a URL and locator. Code extracts with the
   generic extractors. Values are marked `extraction: generic` and can't be **fact** until a
   cross-source check or a Tier 1 quote confirms them. A discovered source that passes checks goes
   into source memory; one that is used often is a candidate for a registry adapter.

### 5.2 Building adapters before URLs are verified
None of the catalog URLs could be checked from the cloud, because the proxy blocks Kenyan sites.
- **Documented APIs are built first:** the World Bank v2 API, the IMF SDMX portal (the legacy
  portal was retired on 2025-11-05) and Kenya Law Gazette pages.
- **`engine catalog probe [--all | <key>] [--save-samples DIR]`** runs on the laptop. It tries each
  adapter's discovery step and records status, final URL, content type and the links found. It
  saves small sample files. The user commits the samples in a clone to `tests/fixtures/real/`;
  they are public documents.
- **EPRA, CBK and KNBS parsers are written against those real samples,** not against guesses.
  Hand-built fixtures exist only for unit tests of the generic layers.

### 5.3 Extraction tiers
1. Spreadsheet or CSV from the publisher (openpyxl in read-only mode; pandas).
2. HTML tables (selectolax).
3. pdfplumber with per-source table settings.
4. Docling, as an optional `[docling]` extra (torch, about 500 MB of models). Adopted only after a
   comparison on real samples on the laptop.
5. OCR: off by default.

The LLM never reads cells for values. A Scout may preview a table (headers plus a few rows, from
the `inspect_table` tool) to choose a locator.

### 5.4 Table checks (accept or quarantine, never quietly fix)
Schema and expected labels · types (`Decimal`) · plausible ranges for each series · totals and
subtotals reconcile · completeness against the previous release's row count · continuity against
stored history (a large jump needs a confirming quote) · cross-extractor agreement when a second
extractor is available · cross-source agreement · vintage (publication date and reference period)
with revisions flagged. A quarantined table keeps its raw file and a reason and yields no numbers.

### 5.5 Store and provenance
- **Blob store:** `~/.kenya-data-engine/blobs/<sha256>`, content-addressed. Raw files are kept once
  and shared across runs. `engine gc --older-than 90d` prunes it.
- **Series store** (SQLite, WAL): rows are append-only by vintage
  `(series, period, entity, metric) → value, unit, published, retrieved_at, blob, page|sheet,
  cell|bbox, extractor@version`. A new vintage with a different value marks a **revision**.
- `engine data fetch <key>` and `engine data show <key>` give direct access, so the store is
  useful before the agents exist.

### 5.6 Stats
pandas, with `Decimal` for money. Change, percentage change, same-entity like-for-like comparisons,
YoY and MoM, rolling averages, and **real terms** (deflated by `knbs.cpi` when available, naming
the base period). Every figure gets an F-label, a formula, its inputs (D-labels) and a unit, all
written to `stats.md`.

**Time:** dates are Africa/Nairobi. The run date goes into every prompt. Kenyan fiscal years
(FY2025/26 = 1 Jul 2025 to 30 Jun 2026) and EPRA cycles (15th to 14th) are first-class period types.

**Units:** unit and currency are always explicit. Nominal and real are labelled. CPI base-year
changes are recorded.

## 6. Claims and verification

### 6.1 Claim writer
No tools. It gets the brief and evidence packets that code selected: E snippets (code picks the
passages relevant to each need, about 6k tokens per source at most), D summaries and F figures,
each labelled. It returns atomic, self-contained claims, each with a template, a quote (for E) or
references (for D and F), a claim type and the entity, metric and period.

### 6.2 Pipeline
| # | Step | Who |
|---|---|---|
| 1 | Candidate claims | LLM |
| 2 | Quote grounding: exact match in the stored, de-hyphenated text (`quote_in_text`). One `ModelRetry` naming the failed quotes. | code |
| 3 | Number check (§6.3) | code |
| 4 | Entity, period and unit match the D provenance or the E context | code |
| 5 | Tier (domain allowlist in config) and vintage; stale limits by claim type (prices 45d, rates 45d, CPI 2 months, annual 18 months; configurable) | code |
| 6 | Entailment: does the quote support the wording? yes / partial / no. Only for claims that passed 2–5. | LLM, one cheap call per claim, batched by 5 |
| 7 | Conflicts: group by (metric, entity, period); resolve higher tier first, then newer period; else unresolved and shown | code (an LLM may draft a one-line note) |
| 8 | Status: **fact** = grounded + numbers + entity/period + entailed yes + Tier 1 (or Tier 2/3 confirmed by Tier 1) + not stale + no unresolved conflict. **inference** = derived, partial, Tier 3 alone, stale, or generic extraction. **speculation** = no direct support (kept only if flagged; never charted). **refused** = failed grounding, numbers or entailment, with the reason. | code |
| 9 | Headline challenge: one targeted search for a primary-source contradiction, using the framing challenge | LLM, reserve budget |

After step 9, the brief's verdict is re-checked: a `supported` story whose headline claim is not a
fact becomes `reframed`, with the reason.

### 6.3 Numbers (hardening)
`tools/numbers.py` parses numbers with their scale and unit: "Sh180.66", "KSh 180.66/litre",
"8 per cent", "1.2bn", "Sh1.2 billion", "1,200,000". A literal number in a claim must equal, **by
value and scale**, a number in its quote. Years and dates must fall within the claim's period or
appear in the quote. Rendered placeholders are exact by construction. This extends the whole-token
rule in `grounding.py`; it doesn't replace it.

### 6.4 Legal and policy claims
`legal_stage` is required, with a date and a Tier 1 source (Parliament, the Gazette, Kenya Law or
the Treasury). "VAT on fuel is 8%" (in force) and "the Finance Bill proposes keeping 8%" (bill)
are different claims, with different stages and statuses. A stage claim from the press alone is at
most an inference.

### 6.5 Sensitive claims
Claims that name a person or allege wrongdoing are `sensitive`. They need a Tier 1 source and an
entailment of `yes`, or they are refused. The engine collects no personal data.

## 7. Budget ledger and efficiency

**Presets** (config, `--budget`):

| | LLM | Search credits | Time |
|---|---|---|---|
| lean | $0.05 | 10 | 2 min |
| standard | $0.15 | 25 | 5 min |
| deep | $0.50 | 60 | 15 min |

**Shares** (standard, soft):
- Planner: 15%, up to 6 searches.
- Scouts: 55% across rounds.
- Claims and verification: 25%. This is a **protected reserve** that Scouts can't spend.
- Headline challenge: 5%. Also protected.

Unspent shares move forward.

**Reservations.** Before a model request, the ledger reserves its worst case (estimated input plus
`max_tokens` at list price) and settles to the actual cost afterwards. Searches reserve credits:
Tavily basic counts 1 and advanced counts 2, and basic is the default. With three Scouts in
parallel, reservations keep the total from ever passing the cap.

**Deadlines:**
- Soft deadline at 60% of the time limit: no new Scout rounds.
- Wrap-up at 80%: in-flight Scouts get the cancellation token.
- Hard limit: write whatever exists as a partial dossier.

**Monthly search quota.** Credits are counted in SQLite per provider and per month.
`engine doctor` shows usage against a configured monthly allowance and warns at 80%.

**Efficiency levers** (each is measured):
- registry and memory hits cost no search;
- search is scoped to domains (`include_domains` for Tavily, `site:` for Serper);
- the shared fetch cache and blob store;
- code selects snippets so prompts stay small;
- a stable prompt prefix for DeepSeek's cache;
- parallel Scouts and fetches;
- gap-driven rounds that stop when the evidence is enough.

**Scorecard** (in `dossier.json`, the README, `engine report` and the TUI):
- cost and searches against the caps;
- facts, inferences and refused counts;
- **facts per dollar**;
- **searches per satisfied need**;
- registry, memory and cache hit rates;
- gap rate;
- time per step.

## 8. Dossier, memory and UX

### 8.1 Folder `briefs/<date>/NN-<slug>/`
| File | Contents |
|---|---|
| `README.md` | Verdict, reader question, top facts (each with a clickable Tier 1 link and page), chart concepts marked **ready / partial / not possible** with the CSV to use, gaps, conflicts, scorecard. Ends with a **15-minute check** list for the human. |
| `brief.md` | The Planner's brief |
| `data/*.csv` | Tidy, chart-ready series, one per chart concept where possible, with source and unit columns |
| `stats.md` | Every F figure, with its formula and inputs |
| `sources.json` | Every source: tier, URL, blob sha256, vintage, extractor, attribution/licence line |
| `gaps.md` | Needs not satisfied, quarantined tables, and why |
| `research/claims.json` | Facts, inferences, speculation, refused, conflicts, evidence |
| `research/comparisons.csv` | Like-for-like history |
| `research/verification.md` | Method log by step, including refusals |
| `dossier.json` | Everything above, machine-readable, with `schema_version`, for Plan 4 |

Reproducibility is recorded in `dossier.json`: engine version, git sha if known, config hash,
prompt hashes, model id, run id.

### 8.2 Memory (SQLite)
- **Source memory:** need signature (metric, entity, unit, frequency) → spec, success and failure
  counts, last verified. Written **only** from sources that passed the checks and are Tier 1 or 2.
  Entries older than 60 days are rechecked before reuse. `engine memory list|forget` lets the user
  inspect and clear it.
- **Topic memory:** topic signature, dossier path, date. `engine run` blends the LLM's novelty score
  with "covered N days ago", computed in code.

### 8.3 CLI
- `engine research <topic> [--budget P] [--plan-only] [--force] [--resume RUN]`
- `engine dossiers list | show <id>`
- `engine data fetch | show <key>`
- `engine catalog probe`
- `engine memory list | forget`
- `engine eval`
- `engine gc`

`engine report` gains the research scorecards.

### 8.4 Engine Room
- **Runs tab:** `r` researches the selected topic.
- **Research view (live):**
  - the ledger as gauges (dollars, credits, seconds, by agent);
  - each need lighting up satisfied, partial or not found, across rounds;
  - claims arriving into fact, inference and refused.
- **Dossiers tab:**
  - list of dossiers;
  - the verdict, the facts with quotes and sources, the gaps and the scorecard;
  - `o` opens a source; `i` inspects the LLM calls.

## 9. Hardening (from the design review)

### 9.1 Untrusted URLs and files
- **URL policy for every fetch:**
  - http(s) only;
  - resolve the host and reject private, loopback and link-local addresses (SSRF);
  - recheck every redirect, with at most 5 redirects;
  - size caps: HTML 5 MB, PDF 25 MB, XLSX 10 MB.
  - Sniff file types from magic bytes, not the extension.
- **Parsing:**
  - pdfplumber and openpyxl run in a thread with a timeout and a page cap (60 pages by default);
  - XLSX is checked for its uncompressed size before loading (zip bombs);
  - a failed parse quarantines the file.
- **Politeness:**
  - at most two concurrent requests per domain, with a minimum delay;
  - robots.txt is honoured for discovered (non-registry) domains;
  - the existing browser-like user agent stays.
- **TLS:** the existing AIA repair only. Never weakened.

### 9.2 Prompt injection
- Fetched text is wrapped as labelled, untrusted data in prompts.
- Agents have no side-effect tools: they can search, fetch and preview, all inside the ledger and
  the URL policy.
- The claim writer has no tools, and verification is code.
- At worst, injected text produces claims that fail grounding or tier rules and are refused.

### 9.3 Concurrency and state
- The ledger is `asyncio`-safe.
- SQLite uses WAL with a single writer task.
- Trace events carry the agent and need id.
- Each checkpoint is written atomically, as `runs.py` does today.

### 9.4 Memory and store integrity
- Memory is written only after verification, with counters, so it can't be poisoned by a single
  bad run.
- The store is append-only, so revisions are visible and never overwritten.

### 9.5 Known model limits
- Model knowledge is never evidence: grounding enforces this.
- The run date is in every prompt, so the model doesn't assume an earlier "now".
- Variance is accepted, as in `engine run`.

## 10. Failure handling
- **A Scout fails or hits its limit:** recorded in `gaps.md`; the dossier continues.
- **A table fails its checks:** quarantined, with its raw file kept.
- **An adapter's discovery fails:** recorded for each source (and visible in `catalog probe`); the
  need falls back to the open path.
- **The LLM is unreachable:** the friendly error from `llm.py`; the dossier stops at its last
  checkpoint.
- **A budget or deadline is hit:** wrap up and write a partial dossier, with the scorecard showing why.
- **A rejected story:** a short dossier with the reasons and the cost.

## 11. Testing and evaluation
- **Offline only, as today:**
  - respx for HTTP;
  - `FunctionModel` for scripted Planner, Scout and claim-writer runs;
  - Textual pilots for the TUI.
- **Unit and property tests:** the ledger (reservations never overshoot under concurrency, with
  reallocation), the number parser, the URL policy, the checks, period types (FY, EPRA cycle) and
  stats.
- **Golden tests:**
  - a fixed evidence set gives exactly the expected facts, inferences and refusals;
  - the fuel VAT case gives a `legal_stage` and refuses the unsupported claim.
- **Real fixtures:** samples from `catalog probe` drive the EPRA, CBK and KNBS parser tests.
- **Gate:** the coverage gate stays at 85%, and `make check` must pass before each commit.
- **`engine eval`:**
  - runs the §12 scenarios live on the laptop;
  - appends each scorecard to SQLite, so engine versions can be compared on cost, facts per
    dollar, gaps and correctness.

## 12. Acceptance (on the user's laptop, `standard` budget)
1. **Fuel dossier.**
   - It finds EPRA's previous schedules on its own.
   - It computes the per-litre change for the same town and fuel in code.
   - It establishes the VAT-on-fuel position with its `legal_stage` and a Tier 1 source.
   - It refuses at least one unsupported claim.
   - It stays within the caps.
2. **CBK rate dossier.** CBR history comes from the registry with no search spent on it.
3. **A weak free-text topic** is rejected cheaply with reasons.
4. **A second fuel dossier** uses fewer searches than the first (memory works).

## 13. Build order (writing-plans splits this into three plans)
- **Plan 2a: data foundation.** Usable on its own via `engine data` and `engine catalog`.
  1. Task 0 spike (`doctor --agents`).
  2. `run_agent` upgrade and the ledger.
  3. URL policy and number parser.
  4. Blob and series store.
  5. Extraction tiers and checks, stats.
  6. World Bank, IMF and Kenya Law adapters.
  7. `catalog probe`.
  8. **Pause:** the user runs the probe and commits samples.
  9. EPRA, CBK and KNBS adapters against the real samples.
- **Plan 2b: research agents.**
  1. Planner, Scouts, rounds and gap check.
  2. Claims and verification.
  3. Dossier and memory.
  4. `engine research`, `dossiers`, `memory`, `eval`.
- **Plan 2c: Engine Room views.** The Research view and the Dossiers tab.

## 14. Risks and open questions
- **DeepSeek tool use plus structured output** is unproven live. Task 0 settles it, and a fallback
  is designed in (§3).
- **Kenyan sites may change layout or block automated access.** Adapters fail soft, the probe shows
  this early, and the open path is the fallback.
- **The Tavily free tier** (about 1,000 credits a month) caps dossiers at roughly 40 a month at
  the standard budget. The scorecard makes the trade-off visible.
- **Docling and OCR** wait for evidence from the laptop comparison.
