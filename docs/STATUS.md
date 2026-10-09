# Kenya Data Engine: project status and handoff

_Last updated 2026-10-09. Read this first in any new session. It is the single source of truth for
what exists, what was decided and why, and what's next._

---

## 1. What we're building, and why

**Jijenga** is the user's personal finance tracking app for Kenyans. The **Kenya Data Engine** is a
local, on-command research engine. It finds *data-worthy* topics in Kenyan economics, personal
finance, startups, business and law, and produces verified research and data for chart-led
explainer content on **LinkedIn and X**.

- **Goal:** awareness and trust. The content is a quiet lead magnet that offers value (clarity,
  context, myth-busting with real numbers) and never pitches the app.
- **The engine is pure research and data.** Writing and visuals happen downstream, in a separate
  editorial agent (Plan 4) and a design agent.
- **Scale:** a founder plus AI agents, not a newsroom. About 2 cents of LLM spend per run and about
  15 minutes of human time per published post. See the "Our context" section of
  `docs/research/2026-10-09-world-class-data-journalism.md`.
- **Success:** never wrong, consistently useful (recurring series), fast (within 24–48h of money
  moments), cheap, and feeding the funnel without pitching.

## 2. Key documents

| Doc | What it is |
|---|---|
| `docs/superpowers/specs/2026-10-09-kenya-data-engine-design.md` | **Approved design spec** for the whole engine (architecture, stages, models, principles) |
| `docs/superpowers/plans/2026-10-09-kenya-data-engine-plan-1-foundation.md` | Plan 1, **done** |
| `docs/superpowers/plans/2026-10-09-kenya-data-engine-plan-3a-engine-room.md` | Plan 3a Engine Room (TUI), **done** except the dropped Performance tab and screenshots |
| `docs/research/2026-10-09-world-class-data-journalism.md` | Global data-journalism benchmarks **reframed for Jijenga**, with an adopt/adapt/skip table, templates, gates, recurring series, and the Plan 2 implications in §7 |
| `README.md` | User guide: install, commands, sources, Engine Room keys, updating, troubleshooting |
| `spikes/RESULTS.md` | Spike notes (Pydantic AI classes used; first live run notes) |

## 3. Non-negotiable principles (from the spec)

1. **Hybrid: code plus LLM.** Code owns flow, fetching, parsing, verification and maths. The LLM
   owns judgement. Agents get their power from tools built in code.
2. **No number from an LLM.** LLMs point to data; code fetches it and computes from it.
   `final_score` is computed in code from the rubric scores.
3. **Grounded claims.** Every claim carries a verbatim quote. `tools/grounding.py` does an
   **exact match** after normalising case, whitespace, typography and punctuation, with no fuzzy
   matching, and numbers must match as whole tokens.
4. **Stage isolation.** Typed Pydantic I/O for each stage, JSON artifacts in `runs/<id>/`, and every
   stage can run on its own and resume.
5. **Fail soft, report loudly.** A failing source, topic or agent is recorded and skipped, never
   silently.
6. **Bounded autonomy.** Budgets per run and per agent.
7. **Secrets never appear** in traces, artifacts, captures, the CLI or the TUI.

## 4. What exists today

Branch `claude/loving-hawking-eucp75` in `TheAccountant777/simple-web-app`. That repo was
retrofitted: the old Flask app was deleted.

**State:** about 439 tests, about 97% coverage, `make check` green, everything pushed.

### Pipeline (`engine run`)
`Radar (code) → Synthesizer (DeepSeek: cluster → rubric scores; code: weighted rank) → ranked topics`

- **Radar** (`radar/`):
  - Adapters for RSS, listing pages (selectolax CSS) and the calendar.
  - Sources live in `defaults/sources.yaml`, with a user override in `~/.kenya-data-engine/sources.yaml`
    that merges by source name.
  - There are 34 sources: 18 enabled and verified live, 16 disabled, each with a note saying why.
  - Per-source health is kept in the SQLite `source_health` table.
  - There is a per-source timeout and user agent.
  - **TLS AIA repair** (`tls.py`) handles government sites with incomplete certificate chains:
    - it follows up to 3 hops;
    - every link must be a current CA that signed the certificate below it;
    - the top link must be signed by a certifi root;
    - partial-chain trust is disabled.
    This is security-reviewed; don't weaken it.
- **Synthesizer** (`synth/`):
  - Signals are given to the LLM as **short labels S1..Sn**, which code maps back to signal ids.
    This stops DeepSeek mis-copying ids.
  - The rubric is `data_ability 0.35`, `wallet_impact 0.20`, `timeliness 0.20`, `clarity_gap 0.15`,
    `novelty 0.10`, with weights in config.
  - Unknown or empty clusters are dropped and recorded.
- **LLM** (`llm.py`):
  - DeepSeek **`deepseek-flash`** (V4.1 Flash) through Pydantic AI `OpenAIChatModel` and
    `OpenAIProvider(base_url)`.
  - **Thinking is disabled**: `extra_body: {thinking: {type: disabled}}`. Thinking mode rejects the
    forced `tool_choice` that structured output uses (live HTTP 400).
  - Friendly errors for 400, 401, 402, 403, 429, 5xx and connection failures.
  - Every LLM exchange is captured, redacted, to `runs/<id>/llm/NNNN-<stage>-<name>.json`.
- **Tools** (`tools/`): `web_search` (Tavily, with Serper fallback), `fetch_page` (trafilatura, with
  a Jina fallback), `read_pdf` (pdfplumber), and `quote_in_text` grounding. These are ready for
  Plan 2's agents.
- **Infrastructure:**
  - `trace.py`: a JSONL tracer with cost, budget, redaction, live `subscribe()` and span attrs.
  - `cache.py`: a SQLite cache.
  - `http.py`: retries, a browser-like user agent, http trace events with `from_cache`, AIA.
  - `runs.py`: atomic artifacts and run-id validation.
  - `pipeline.py`: resume, where an upstream rerun forces downstream reruns, and a persisted budget.

### CLI (`engine …`)
| Command | Purpose |
|---|---|
| `init` | Set up the home folder. Asks for keys only on first run or with `--keys`. `--reset-config` backs up and writes a short override-only `config.yaml`. |
| `doctor` | Health of keys, LLM, search and every source |
| `run` | Full pipeline. Flags: `--top`, `--since`, `--resume`, `--json` |
| `stage radar\|synthesize` | Run one stage |
| `sources list\|test <name>\|--all` | Source audit loop, with `--json` |
| `report` | Speed, cost, cache hit rate and source reliability across runs |
| `compare [RUN…] --last N` | Multi-run consensus: topics matched by shared signals, with appearances k/N, mean, range, mean rank, and strong/mixed/noise |
| `tui` (alias `browse`) | Engine Room |

### Engine Room TUI (`tui/`, Textual)
- **Live:** stage boxes, source tiles, an event stream, a cost gauge, and the ▶ New run, ↻ Run
  again and ▶▶ Run ×N controls.
- **Runs:** the score maths (criterion × weight = points, summing to the final score ✓), the
  justification, why now, the signals, and an **LLM inspector** (`i`).
- **Sources:** health, `t` or `T` to test live, `/` to filter.
- **Compare:** `space` to select runs, `c` to compare.
- The **Performance tab was dropped** at the user's request; `engine report` covers it.

## 5. Live findings (the user's laptop, 2026-10-09)

- **First live run:** 96 signals, cost $0.011. Fuel/EPRA ranked #1.
- **After the source upgrade:** 194 signals from 17 of 18 sources, about $0.02–0.05 per run, about
  47s. Synthesize takes about 45s; Radar about 2.4s.
- **Topic quality is good:** fuel and EPRA, the CBK rate hold at 8.75%, the bond-trading slowdown,
  remittances and VASP rules, the milk shortage.
- **Variance across 4 runs:** #1 (fuel) is stable at 4.55–4.65. Places #2–5 shuffle, because
  clustering differs and topics sit close together around 3.95–4.15. **The user decided to keep the
  default temperature (variability is OK)** and to use `compare` for consensus.
- **KNBS:** the TLS chain repair works; the remaining failure is a 404 on `/recent-releases/`, which
  needs a URL audit.
- **Google Trends KE RSS works.** An external research agent wrongly claimed it was dead.

## 6. Environment and workflow facts

- **The user runs the engine locally** at `~/.kenya-data-engine`, with DeepSeek, Tavily and Serper
  keys. **The cloud session has no API keys**, and its **proxy blocks Kenyan sites**, so live checks
  happen on the user's laptop.
- **Update loop:** `uv tool install --reinstall git+https://github.com/TheAccountant777/simple-web-app@claude/loving-hawking-eucp75`
- **Collaboration preferences:**
  - Be efficient with tokens.
  - Delegate implementation to **Sonnet subagents** in batches, with Opus coordinating and
    reviewing.
  - Fold reviews together where risk is low.
  - Show renders and outputs for UX.
  - The user answers tersely; confirm understanding briefly.
- **Git:**
  - Develop and push on `claude/loving-hawking-eucp75` only.
  - A local merge into `main` was done earlier, but **`main` has not been pushed**; the user hasn't
    said "push main".
  - Merge or PR when the user asks.
- **Commit trailers:**
  ```
  Co-Authored-By: Claude <model> <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BZbyYiQ1AmtoKWqSaEYP8z
  ```

## 7. Next: Plan 2, research and verification (not yet written)

Write it with superpowers:writing-plans from spec §§4.3–4.6, upgraded by research doc §0 and §7:

1. **Generalise `run_agent` first.** Add `deps` and RunContext access for tools, Pydantic AI
   `UsageLimits` (about 15 tool calls), a budget check per model request, and per-agent budgets from
   config. This was the final reviewer's item I4.
2. **Planner agent** (with tools: search, site search, fetch, PDF, memory). Its output:
   - `ResearchBrief`, with a reader-question `core_question`;
   - 2–3 angles, including a contrarian one, plus a **framing challenge** ("what would make this
     wrong?");
   - `chart_concepts`, each tagged with its FT relationship type;
   - a **story verdict**: `supported | reframed | reject`, with reasons.
3. **Data Scout:** one worker per data requirement, running in parallel.
   - The worker returns a `DataSourceSpec`; code extracts the data (adapter, CSV, XLSX, HTML table,
     pdfplumber, then Docling for complex PDFs).
   - **Bulletproofing:** schema and range checks, spot-check, cross-source, **totals reconciliation**,
     **coverage gaps**, **vintage and revisions**.
   - Statistics are computed in pandas, never by the LLM.
4. **Dossier output** (`briefs/<date>/NN-<slug>/`): `brief.md`, `data/*.csv`, `stats.md`,
   `sources.json`, `gaps.md`, and `research/`. The `research/` folder holds:
   - `claims.json`, with a status of fact, inference or speculation for each claim, plus quote,
     vintage and conflicts;
   - `comparisons.csv`, with like-for-like history;
   - `verification.md`, the method log.
5. **Memory:** topics covered, which makes **novelty computable in code**; source reliability.
   Optional.
6. **`engine research "<topic>"`**, plus TUI support for viewing dossiers.
7. **Acceptance test: the fuel dossier.** The engine must:
   - find EPRA's previous price schedules on its own;
   - compute the per-litre change for the same town and fuel;
   - establish the actual status of the VAT proposal from primary sources (Parliament and the
     National Treasury), resolving whether it *maintains* 8% or is a new cut;
   - refuse unsupported claims.
8. **The data catalog** for the Data Scout comes from the external agent's Part B table (CBK
   DataTables pages, KNBS CPI and LEI, EPRA PDFs, CMA bulletins, World Bank and IMF APIs). Re-verify
   it live, because some of that agent's claims were wrong.

### Later
- **Plan 4, editorial agent.** It reads dossiers and has no web access. It produces LinkedIn and X
  copy plus a chart spec, using the research doc's templates and two gates (evidence, then craft).
  ChatGPT is drafting the editorial playbook, documents 05 → 04 → 01 → 02 → 03.
- **Design agent.** Fills chart and carousel templates from a chart spec, at 1080×1350.

## 8. Deferred minors (fix opportunistically)
- `normalize_url`: the `utm_` match is case-sensitive and query params are not sorted. The
  `new_run` mkdir has a race under concurrency.
- `fetch`: on exhausted 5xx retries, the status is None. trafilatura runs synchronously inside an
  async function; use `to_thread` once fetches run concurrently.
- `AdapterError` drops the `FetchError` hint. RSS `bozo` (an HTML or captcha page served as 200)
  isn't traced.
- dateutil `fuzzy` fills missing date parts from today's date.
- LLM capture filenames don't sanitise `stage` or `name`. A resumed run's duration spans the gap.
- `engine run --top/--since` are silently ignored for stages skipped by `--resume`.
- The source-test `T` key in the TUI runs sources one at a time.
- Batches B and C of the TUI had no separate code review (all checks green); a cheap Sonnet review
  is optional.
