# Kenya Data Engine — Design Spec

- **Date:** 2026-10-09
- **Status:** Draft for review
- **Working name:** `kenya-data-engine` (CLI: `engine`)

---

## 1. Purpose

A local, on-command research engine that surfaces **data-worthy topics** in Kenyan economics, personal finance, startups, business and law, and delivers for each one a **verified research brief plus an analysis-ready data pack**.

It feeds a human-led content process: a person curates the topics, then a separate system handles writing and data visualisation for LinkedIn and X. The content serves as awareness and a lead magnet for a personal-finance-tracking SaaS by **offering value: explaining, demystifying, and giving clarity and context**, without pitching the product.

**The engine's job is pure research and data.** It does not write posts or draw charts.

### Success criteria

1. One command (`engine run`) produces a dated dossier of ranked topics. Each topic has a brief, tidy CSVs, key stats and sources.
2. Every number in a dossier traces to a source URL and was computed by code, not by an LLM.
3. Every qualitative claim carries a verbatim quote that code has confirmed exists in the cited source.
4. A 5-topic run stays under a hard cap of US$0.50 in LLM spend (the expected figure is about $0.25; see §11) and stays inside free search tiers at a few runs per week.
5. Each stage can be run, tested and improved on its own, against saved inputs.

### Non-goals (v1)

- Writing post copy, captions or threads.
- Rendering charts or visuals.
- Publishing or scheduling to social platforms.
- A web UI, a hosted service or a scheduler. It runs locally, on command.
- Countries other than Kenya.
- The X/Twitter API. X is reachable only via web search.

---

## 2. Design principles

1. **Hybrid: code plus LLM.** Code owns the flow, fetching, parsing, verification and maths. The LLM owns judgement: clustering, scoring, angles, and finding where data lives. **Agents are empowered by code through tools.**
2. **No number from an LLM.** LLMs point to data; code fetches it and computes from it. Unsourced figures are dropped or flagged.
3. **Grounded claims.** Every qualitative claim carries a verbatim quote, and code verifies the quote exists in the fetched source.
4. **Stage isolation.** Each stage has typed input and output (Pydantic), persists JSON to disk, has its own CLI command and is tested independently. The orchestrator only chains stages.
5. **Context engineering.** Workers get tight task cards and return compressed, structured results, never their transcripts. Plans and checklists live on disk, not in context.
6. **Fail soft, report loudly.** A failing adapter, topic or worker is logged and skipped. The dossier states what failed and why. "Not found" is a legitimate result.
7. **Bounded autonomy.** Every agent has step, token and cost budgets.

---

## 3. Architecture

```
engine run
  │
  ├─ 1. Radar          (code)              → signals.json
  ├─ 2. Synthesizer    (LLM, structured)   → topics.json
  ├─ 3. Planner        (agent + tools)     → briefs/<topic>.json      ┐ parallel
  ├─ 4. Data Scout     (orchestrator +     → datapacks/<topic>/       ┘ per topic
  │                     parallel workers + code verification)
  └─ 5. Dossier        (code)              → briefs/<date>/ (MD + CSV)
                 ▲                                   │
                 └──────── 6. Memory (SQLite) ◄──────┘
```

All intermediate artifacts are written to `runs/<run-id>/`, where run-id is `YYYY-MM-DD-HHMM`. Any stage can be re-run from the artifacts of the previous stage.

### Core data models (Pydantic)

| Model | Key fields |
|---|---|
| `Signal` | `id`, `kind` (news / data_release / policy / attention / calendar), `title`, `source`, `url`, `published_at`, `snippet`, `meta` |
| `Topic` | `id`, `title`, `summary`, `why_now`, `signal_ids[]`, `scores{data_ability, wallet_impact, timeliness, clarity_gap, novelty}`, `final_score`, `category` (economy / personal_finance / startups / business / law) |
| `Claim` | `text`, `source_url`, `quote`, `grounded: bool` |
| `DataRequirement` | `id`, `metric`, `time_range`, `granularity`, `candidate_sources[]`, `purpose` |
| `ResearchBrief` | `topic_id`, `core_question`, `angles[]` (2–3, including one contrarian or myth-busting), `claims_to_check[]: Claim`, `data_requirements[]`, `chart_concepts[]` (type, x, y, insight), `context_notes[]: Claim` |
| `DataSourceSpec` | `requirement_id`, `url`, `format` (adapter / csv / xlsx / html_table / pdf), `locator` (adapter call, table index or page number), `units`, `notes` |
| `Dataset` | `requirement_id`, `csv_path`, `source: DataSourceSpec`, `retrieved_at`, `verification{schema_ok, spot_check_ok, cross_source: agree / disagree / n/a}` |
| `Stat` | `label`, `value`, `unit`, `formula`, `dataset_ids[]` |
| `DataPack` | `topic_id`, `datasets[]`, `stats[]`, `gaps[]` (requirement_id, reason), `confidence` (green / amber / red) |

---

## 4. Stages

### 4.1 Radar (code only)

Each source is an adapter implementing `fetch(since: datetime) -> list[Signal]`. Adapters are independent: one failing logs a warning and the run continues. Signals are de-duplicated by URL and by normalised title.

**v1 adapters:**

| Adapter | Signal kind | Method |
|---|---|---|
| `news_rss` | news | RSS from Business Daily, Nation, The Standard, The Star, Capital FM Business and Kenyans.co.ke. Feed URLs are configurable. |
| `cbk` | data_release | Monetary Policy Committee decisions, T-bill and bond auction results, exchange rates, remittances |
| `knbs` | data_release | CPI and inflation releases, GDP and Economic Survey announcements |
| `epra` | data_release | Monthly pump price review (14th), electricity tariff notices |
| `parliament` | policy | Bills tracker (Finance Bill and others) |
| `calendar` | calendar | `calendar.yaml`: known moments within a look-ahead window (default 21 days) |
| `google_trends` | attention | Kenya trending searches. **Optional and allowed to fail** (unofficial access). |

**Later adapters:** CMA (money market funds), NSE, SASRA, RBA, Kenya Gazette and Kenya Law, Treasury, KRA, TechCabal, Disrupt Africa, Kenyan Wall Street, Africa: The Big Deal, Reddit r/Kenya.

### 4.2 Synthesizer (structured LLM, no tools)

- **Input:** `Signal[]`, plus recent topics from Memory.
- **Step 1, clustering.** DeepSeek groups signals into candidate topics, with JSON output validated against the schema. Batches are sized to keep prompts compact.
- **Step 2, scoring.** For each candidate, DeepSeek scores 1–5 on each rubric criterion with a one-line justification:

| Criterion | Question | Default weight |
|---|---|---|
| `data_ability` | Can this be charted with real, obtainable numbers? | 0.35 |
| `wallet_impact` | Does it touch everyday Kenyan money? | 0.20 |
| `timeliness` | Why now? Is there a release, debate or deadline? | 0.20 |
| `clarity_gap` | Is there confusion or misinformation we can clear up? | 0.15 |
| `novelty` | Have we not covered this recently? This is informed by Memory. | 0.10 |

- **Code** computes `final_score` from weights in `config.yaml`, applies a Memory penalty for topics covered within N days, and keeps the top N (default 5).
- **Reasoning effort:** low.
- **Output:** `topics.json`.

### 4.3 Planner (agent with tools)

- **Input:** one `Topic`. Topics run in parallel, with a concurrency limit from config.
- **Tools:** `web_search`, `site_search`, `fetch_page`, `read_pdf`, `memory_lookup`, `submit_brief`.
- **Behaviour:** the agent investigates what's being said, what's misunderstood and what data exists. It then calls `submit_brief` with a `ResearchBrief`.
- **Grounding:** each `Claim` must include a verbatim `quote` from a page fetched during the session. Code checks the quote against the cached page text (normalised whitespace and case, with fuzzy match ≥ 0.9). Ungrounded claims are dropped, and the count is recorded.
- **Budgets:** about 15 tool calls, a token cap and a cost cap per topic (all configurable).
- **Reasoning effort:** high.
- **Failure handling:** a schema-invalid submission gets one repair turn. If it is still invalid, or the budget is exhausted, the topic is marked failed, which is recorded and the run continues.
- **Output:** `briefs/<topic-id>.json`.

### 4.4 Data Scout (orchestrator, parallel workers, code)

The brief's `data_requirements` are the checklist, persisted on disk.

1. **Locate: one isolated worker agent per requirement, run in parallel.**
   - Each worker gets a task card: the requirement, preferred sources, trusted domains, the expected output schema and a budget.
   - Tools: the source adapters' query functions, `site_search`, `web_search`, `fetch_page`, `read_pdf`.
   - Each worker returns only a `DataSourceSpec`, or `not_found` with a reason.
2. **Extract: code first.** Adapter, CSV, XLSX and HTML-table sources are parsed by code. For PDF tables, the fast path is pdfplumber, the complex path is Docling, and the LLM-assisted path is a last resort. With the LLM path, the extracted text is given to the LLM to structure into rows, and every row must cite its page.
3. **Verify (code):**
   - **Schema and sanity:** the expected columns and units, no impossible values, and dates inside the range.
   - **Spot-check:** a sample of k values (default 5) is located again in the source text or table. A mismatch flags the dataset.
   - **Cross-source:** where config maps a metric to two sources (for example, inflation from CBK and from KNBS), the latest values are compared within a tolerance. Disagreement is reported, never hidden.
4. **Compute (code, pandas):** key stats such as period-over-period change, cumulative change, highs and lows, averages and ranks. Each `Stat` records its formula and source `dataset_ids`.
5. **Gaps:** unmet or unverified requirements go into `gaps[]` with reasons.

- **Confidence:** green means all requirements are met and verified; amber means at least half are verified; red means fewer than half are verified.
- **Reasoning effort:** low for locate and extract.

### 4.5 Dossier (code only)

```
briefs/2026-10-09/
  index.md                     ranked topics: title, why-now, score, confidence badge, link
  01-<topic-slug>/
    brief.md                   question, angles, grounded claims, chart concepts, context
    data/<requirement-id>.csv  tidy, analysis-ready
    stats.md                   key numbers, each with formula and source link
    sources.json               every URL, retrieved_at, what was taken from it
    gaps.md                    missing or unverified items and why
```

- Markdown and CSV are the human and hand-off formats.
- JSON is for system records (`runs/`) and `sources.json`.

### 4.6 Memory (SQLite, `data/engine.db`)

| Table | Purpose |
|---|---|
| `topics_covered` | topic title, keywords, angles, run date. Feeds the novelty score and the repeat penalty. |
| `source_reliability` | per domain and adapter: verification passes, failures and disagreements, giving a rolling score that is used to rank candidate sources |
| `cache` | fetched pages and files: URL, hash, fetched time and TTL (time-to-live, set per source) |
| `performance` | **Later:** manual post-performance entries to feed scoring |

---

## 5. Agent toolbelt

All tools are plain Python functions with typed signatures, registered with the agent framework. Every tool call is cached and traced.

| Tool | Purpose |
|---|---|
| `web_search(query, n)` | A provider-agnostic interface. Tavily is the default, Serper the fallback, and SearXNG is optional. |
| `site_search(domain, query)` | Search limited to trusted domains |
| `fetch_page(url)` | httpx plus trafilatura to clean text, with Jina Reader as the fallback for JS-heavy pages. The text is cached for grounding checks. |
| `read_pdf(url, pages?)` | pdfplumber text and tables, with Docling for complex tables. Results are cached. |
| `adapter.<source>.<query>()` | Structured fetchers, for example `cbk.exchange_rates(start, end)` and `epra.pump_prices(start, end)` |
| `memory_lookup(query)` | Past topics and angles |
| `submit_brief` / `submit_source_spec` | Terminal tools that carry the typed output |

---

## 6. CLI

```
engine run [--top N] [--resume RUN_ID]      full pipeline
engine research "<topic text>"              skip discovery; plan + scout a given topic
engine stage radar|synthesize|plan|scout|dossier --input <path> [--out <path>]
engine doctor                               live health check of every adapter and key
engine report [RUN_ID]                      cost, time, failures, source stats
engine eval [--suite default]               run eval suite against fixed topics
```

---

## 7. Configuration

- **`.env`:** `DEEPSEEK_API_KEY`, `TAVILY_API_KEY`, and optionally `SERPER_API_KEY` and `JINA_API_KEY`.
- **`config.yaml`:**
  - `top_n`, concurrency
  - scoring weights, novelty window
  - per-stage model and reasoning effort
  - per-agent budgets (steps, tokens, US$), run cost cap
  - enabled adapters, feed URLs, search provider order
  - cross-source metric map
  - cache TTLs
- **`calendar.yaml`:** recurring and dated Kenyan moments, each with a name, date rule, category and note.
- **`prompts/*.md`:** version-controlled prompts per agent. Each contains a Kenya context primer, the rubric or output schema, a worked example and the hard rules.

---

## 8. Reliability and observability

- **Retries:** HTTP and LLM calls retry with exponential backoff and jitter. Invalid structured output gets one repair turn.
- **Resume:** `--resume` skips stages whose output artifacts exist and are valid.
- **Budgets:** per-agent step, token and cost caps, plus a run-level cost cap that halts new LLM work and still produces a partial dossier.
- **Tracing:** `runs/<id>/trace.jsonl` holds one record per LLM call and tool call: stage, topic, tokens, cost, latency, status. It uses OpenTelemetry GenAI-compatible field names, with optional OTel export to a dashboard such as Logfire.
- **Run summary:** `runs/<id>/summary.json` records counts, costs and failures per stage, and `engine report` renders it.

---

## 9. Testing and evals

| Layer | Approach |
|---|---|
| Pure code (parsers, verification, scoring maths, stats, grounding matcher) | pytest unit tests |
| Adapters | Recorded real responses (HTML, PDF, RSS fixtures). Tests run offline and give the same result every time. `engine doctor` covers live drift. |
| Stages | The agent framework's fake/function model returns scripted outputs and tool calls, so stage logic is tested with zero API calls. |
| Orchestrator | An end-to-end test on fixtures plus the fake model, checking that artifacts exist and resume works |
| Agent quality | `engine eval`: a fixed suite of about 5 known topics. It scores briefs (grounding rate, angle quality via rubric) and data packs (requirements met, verification pass rate, cost). Run it whenever prompts change. |

---

## 10. Stack

| Layer | Choice |
|---|---|
| Language and tooling | Python 3.12, uv, ruff, pytest |
| Orchestration | Plain Python with asyncio |
| Agent loops | Pydantic AI |
| LLM | DeepSeek V4.1 Flash (`deepseek-flash`) through its OpenAI-compatible API, with per-stage reasoning effort |
| Search | Tavily (default) and Serper (fallback), behind one interface |
| Web extraction | httpx plus trafilatura, with Jina Reader as the fallback |
| PDF | pdfplumber, plus Docling for complex tables |
| Data and storage | pandas, SQLite |
| CLI and config | Typer, pydantic-settings, PyYAML |
| Tracing | JSONL, with optional OTel export |

### Early validation spikes (throwaway)

1. **Pydantic AI and DeepSeek:** tool calling, structured output and reasoning-effort control with thinking mode on.
2. **PDF extraction:** pdfplumber versus Docling on 3 real KNBS and CBK PDF tables.
3. **Source access:** confirm feeds and page structures for CBK, KNBS, EPRA, Parliament and the news RSS sources.

If a spike fails, the affected choice is revisited before that component is built.

---

## 11. Cost estimate

These are assumptions, to be measured in the first runs. A 5-topic run uses about 75 searches. Tavily's free tier is about 1,000 credits a month, enough for roughly 3 runs a week. DeepSeek spend is estimated at under US$0.25 per run at off-peak rates of about $0.15 per million input tokens and $0.60 per million output tokens. Pricing is to be confirmed on the providers' own pages.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Kenyan government sites are unreliable or PDF-only | Fail-soft adapters, a cache, `engine doctor`, and gaps surfaced in the dossier |
| LLM invents numbers or claims | Numbers come only from code; quotes are verified against the source; there is a cross-source check |
| Agents run away on cost | Per-agent and per-run budgets |
| Search free tiers change | Pluggable providers, SearXNG option |
| Google Trends access breaks | Treated as optional |
| Prompt changes degrade quality silently | The eval suite runs on every prompt change |

---

## 13. Build order (for the implementation plan)

1. Project skeleton: models, config, tracing, cache, CLI shell, stage I/O, orchestrator with resume.
2. Validation spikes 1–3.
3. Toolbelt: search, fetch, PDF, grounding matcher.
4. Radar: RSS, calendar, CBK, KNBS, EPRA, Parliament, Google Trends.
5. Synthesizer.
6. Planner.
7. Data Scout, including verification and stats.
8. Dossier.
9. Memory integration.
10. `doctor`, `report` and `eval`.
