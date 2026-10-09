# Plan 2a: Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the budget ledger, the agent-runner upgrade, safe fetching, number and period
parsing, the blob and series store, extraction, table checks, stats, the series registry with the
World Bank, IMF SDMX and listing adapters, and the `engine data`, `engine catalog probe` and
`engine gc` commands. All of it works on its own, before any research agent exists.

**Architecture:** New `research/` (ledger only in this plan) and `data/` packages, plus hardening in
`http.py`, `llm.py` and `tools/search.py`. Code owns every number. The LLM is touched only by the
`run_agent` upgrade and the `doctor --agents` smoke test.

**Tech Stack:** Python 3.12, uv, Pydantic v2, Pydantic AI 2.54 (`UsageLimits`, `WrapperModel`),
httpx, pdfplumber, openpyxl (new), pandas (new), selectolax, SQLite (WAL), Typer + Rich, pytest +
respx.

**Spec:** `docs/superpowers/specs/2026-10-09-plan-2-research-dossier-design.md` (read §§3, 5, 7,
9). Background: `docs/research/2026-10-09-table-extraction.md`,
`docs/research/2026-10-09-kenya-data-catalog.md`.

## Global Constraints

- `make check` (ruff, ruff format check, strict mypy on `src`, pytest with an 85% coverage gate)
  passes before every commit.
- Tests never touch the network: respx for HTTP, `FunctionModel` for the LLM.
- No number from an LLM. Values are `Decimal` from extraction, never floats parsed by a model.
- Never weaken TLS (`tls.py`). Secrets never reach traces, artifacts, captures, the CLI or the TUI.
- Existing behaviour of `engine run`, `radar`, `synth` and their tests stays unchanged. New
  parameters default to today's behaviour.
- Config models extend `_Strict` (unknown keys are errors). Defaults live in
  `defaults/config.yaml`, and new packaged YAML is added to the hatch `include` list.
- Dates are Africa/Nairobi for display and periods. Timestamps are stored as UTC ISO.
- Budget presets: lean `$0.05 / 10 credits / 120 s`, standard `$0.15 / 25 / 300 s`, deep
  `$0.50 / 60 / 900 s`. Shares: planner 0.15, scouts 0.55, claims 0.25, challenge 0.05; claims
  and challenge are protected. Search shares: planner 0.25, scouts 0.65, claims 0, challenge
  0.10. Tavily basic costs 1 credit, advanced costs 2.
- Size caps: HTML 5 MB, PDF 25 MB, XLSX/CSV 10 MB. At most 5 redirects. PDF page cap 60.
  Parse timeout 30 s.
- Commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01BZbyYiQ1AmtoKWqSaEYP8z`.

## Review Focus

1. **Parallel spend never overshoots.** Three concurrent reservations near the cap: the third is
   refused rather than overshooting. Pinned in Task 2.
2. **Redirects to private addresses.** A public URL that 302s to `http://127.0.0.1/` or
   `http://169.254.169.254/` is blocked, as is one that resolves to `10.x`. Pinned in Task 5.
3. **Kenyan number formats.** "Sh1.2bn", "KSh 180.66", "8 per cent", "1,234.5", "(3.2)" as a
   negative, and "–" or "n/a" as missing all parse correctly, and "19.5" never matches "9.5".
   Pinned in Task 6.
4. **Messy spreadsheets.** Merged multi-row headers, footnote rows, thousands separators in text
   cells and a blank leading column don't misalign values. Pinned in Task 9.
5. **A revision is not a duplicate.** Fetching the same period again with a changed value keeps both
   vintages and flags a revision; the same value is idempotent. Pinned in Task 8.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kenya_data_engine/config.py` (modify) | `ResearchConfig`, `BudgetPreset`, `Pricing.cache_hit_input_per_m`, `DataConfig` |
| `src/kenya_data_engine/trace.py` (modify) | `cost_of(..., cache_read_tokens)` |
| `src/kenya_data_engine/research/__init__.py`, `research/budget.py` | Ledger |
| `src/kenya_data_engine/llm.py` (modify) | `run_agent` deps, limits, ledger; `LedgerModel` |
| `src/kenya_data_engine/tools/search.py` (modify) | Credit charging and monthly quota |
| `src/kenya_data_engine/cli/doctor.py` (modify) | `--agents` smoke test |
| `src/kenya_data_engine/tools/urlpolicy.py` | URL safety and magic-byte sniffing |
| `src/kenya_data_engine/http.py` (modify) | Opt-in policy, manual redirects, size cap |
| `src/kenya_data_engine/tools/numbers.py` | Number parsing |
| `src/kenya_data_engine/data/periods.py` | Period types |
| `src/kenya_data_engine/data/models.py` | `Provenance`, `Observation`, `RawTable`, `CheckReport`, `SeriesSpec` |
| `src/kenya_data_engine/data/store.py` | `BlobStore`, `SeriesStore` |
| `src/kenya_data_engine/data/extract/{__init__,sheets,html,pdf}.py` | Extractors returning `RawTable` |
| `src/kenya_data_engine/data/checks.py` | Table checks |
| `src/kenya_data_engine/data/stats.py` | Figures with formula logs |
| `src/kenya_data_engine/data/registry.py`, `data/adapters/{base,worldbank,sdmx,listing}.py` | Registry and adapters |
| `src/kenya_data_engine/defaults/catalog.yaml` | Registry entries and probe candidates |
| `src/kenya_data_engine/cli/data.py`, `cli/catalog.py`, `cli/gc.py` | Commands |

---

### Task 1: Research config, cache-hit pricing

**Files:**
- Modify: `src/kenya_data_engine/config.py`, `src/kenya_data_engine/defaults/config.yaml`,
  `src/kenya_data_engine/trace.py`, `src/kenya_data_engine/llm.py` (pass cache reads)
- Test: `tests/test_config.py`, `tests/test_trace.py`

**Interfaces:**
- Produces:
  - `BudgetPreset(_Strict)`: `usd: float`, `search_credits: int`, `seconds: float`.
  - `ResearchConfig(_Strict)`:
    - `default_budget: Literal["lean","standard","deep"] = "standard"`
    - `presets: dict[str, BudgetPreset]`
    - `shares: dict[str, float]`, `search_shares: dict[str, float]`, `protected: list[str]`
    - `scout_concurrency: int = 3`
    - `monthly_search_credits: int = 1000`
  - `DataConfig(_Strict)`: `max_bytes: dict[str, int]` (keys html, pdf, sheet),
    `max_redirects: int = 5`, `pdf_max_pages: int = 60`, `parse_timeout_s: float = 30`,
    `per_domain_concurrency: int = 2`.
  - `EngineConfig.research: ResearchConfig` and `EngineConfig.data: DataConfig`, both
    defaulted from YAML.
  - `Pricing.cache_hit_input_per_m: float | None = None`.
  - `Tracer.cost_of(input_tokens, output_tokens, cache_read_tokens: int = 0) -> float`.
    Cache-read tokens are priced at `cache_hit_input_per_m` (input price when None); the rest of
    the input at the input price.
  - `Tracer.record_llm(..., cache_read_tokens: int = 0)` stores `cache_read_tokens` in attrs.
    The Tracer constructor gains `cache_hit_per_m: float | None = None`, and `open_context`
    passes it.

- [ ] **Step 1: Write the failing tests**
  - `test_research_presets_defaults`:
    - `load_config(tmp_home).research.presets["standard"] == BudgetPreset(usd=0.15, search_credits=25, seconds=300)`
    - lean and deep match Global Constraints
    - `sum(shares.values()) == pytest.approx(1.0)`
    - `protected == ["claims", "challenge"]`
  - `test_research_shares_must_sum_to_one`: a user override with shares summing to 0.9 raises
    `ConfigError`. Use a validator, as the weights do today.
  - `test_cost_of_cache_reads`: Tracer with in 0.15, out 0.60, cache 0.015;
    `cost_of(1_000_000, 0, cache_read_tokens=400_000) == pytest.approx(0.6*0.15 + 0.4*0.015)`.
    With cache None, the result equals `cost_of(1_000_000, 0)`.
- [ ] **Step 2: Run** `uv run pytest tests/test_config.py tests/test_trace.py -q --no-cov`. Expected: FAIL.
- [ ] **Step 3: Implement** the models, the YAML defaults and the Tracer change. In `run_agent`,
  pass `usage.cache_read_tokens` to `record_llm`.
- [ ] **Step 4: Run** the same tests, then `make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(config): research budgets, data limits, cache-hit pricing`.

### Task 2: Budget ledger

**Files:**
- Create: `src/kenya_data_engine/research/__init__.py`, `src/kenya_data_engine/research/budget.py`
- Test: `tests/test_budget.py`

**Interfaces:**
- Consumes: `BudgetPreset`, `ResearchConfig` (Task 1).
- Produces:

```python
GROUPS = ("planner", "scouts", "claims", "challenge")   # order = reallocation order

class BudgetExceeded(EngineError): ...                  # errors.py style, with a hint

@dataclass(frozen=True)
class Reservation: id: int; group: str; usd: float

class Phase(StrEnum): RUNNING="running"; SOFT="soft"; WRAP_UP="wrap_up"; EXPIRED="expired"

class GroupState(BaseModel): usd_cap: float; usd_spent: float; usd_reserved: float
                             credits_cap: int; credits_spent: int; closed: bool
class LedgerSnapshot(BaseModel): preset: str; usd_total: float; usd_spent: float
                                 credits_total: int; credits_spent: int
                                 elapsed_s: float; seconds_total: float; phase: Phase
                                 groups: dict[str, GroupState]

class Ledger:
    def __init__(self, preset_name: str, preset: BudgetPreset, cfg: ResearchConfig,
                 clock: Callable[[], float] = time.monotonic) -> None
    @classmethod
    def from_config(cls, cfg: ResearchConfig, preset_name: str | None = None) -> "Ledger"
    def reserve(self, group: str, usd: float) -> Reservation    # raises BudgetExceeded
    def settle(self, r: Reservation, actual_usd: float) -> None  # spent += actual; reserved -= r.usd
    def charge_credits(self, group: str, credits: int) -> None  # raises BudgetExceeded
    def close(self, group: str) -> None   # leftover USD and credits move to the next open group in GROUPS
    def phase(self) -> Phase               # elapsed/seconds: <0.6 RUNNING, <0.8 SOFT, <1.0 WRAP_UP, else EXPIRED
    def remaining_usd(self, group: str) -> float   # cap - spent - reserved
    def snapshot(self) -> LedgerSnapshot
```

Caps are `share × total`. Credits use `search_shares`, floored, with the remainder going to
scouts. Protected groups are never drawn on by other groups. `reserve` also refuses when the phase
is EXPIRED. All methods are synchronous (no `await` inside), so they're atomic under asyncio.

- [ ] **Step 1: Write the failing tests** (`tests/test_budget.py`), using a fake clock:
  - `test_caps_from_standard`:
    - planner usd cap 0.0225, scouts 0.0825, claims 0.0375, challenge 0.0075
    - credits planner 6, scouts 17, claims 0, challenge 2 (25 total; the floor remainder goes to scouts)
  - `test_reserve_settle_accounting`: reserve 0.01, then settle 0.004, then `remaining_usd`
    rises by 0.006.
  - `test_parallel_reservations_never_overshoot`: three reservations of 0.03 in scouts (cap
    0.0825). The first two succeed and the third raises `BudgetExceeded`; spent + reserved stays
    ≤ cap. *(Review Focus 1)*
  - `test_protected_groups_untouched`: exhausting scouts leaves claims at 0.0375 remaining.
  - `test_close_reallocates_forward`: planner spends 0.01 and closes; the scouts cap becomes
    0.0825 + 0.0125, and planner credits move to scouts.
  - `test_phases`: clock at 0.59, 0.6, 0.8 and 1.0 of 300 s gives RUNNING, SOFT, WRAP_UP and
    EXPIRED; `reserve` in EXPIRED raises.
  - `test_charge_credits_over_cap_raises`.
- [ ] **Step 2: Run** `uv run pytest tests/test_budget.py -q --no-cov`. Expected: FAIL (import).
- [ ] **Step 3: Implement** `research/budget.py`, with `BudgetExceeded` in `errors.py`.
- [ ] **Step 4: Run** the tests, then `make check`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(research): budget ledger with reservations and reallocation`.

### Task 3: `run_agent` upgrade, `LedgerModel`, search credits and quota

**Files:**
- Modify: `src/kenya_data_engine/llm.py`, `src/kenya_data_engine/tools/search.py`
- Test: `tests/test_llm.py`, `tests/test_search.py`

**Interfaces:**
- Consumes: `Ledger`, `BudgetExceeded` (Task 2); `Tracer.cost_of` (Task 1).
- Produces:

```python
class LedgerModel(WrapperModel):
    def __init__(self, wrapped: Model, ledger: Ledger, group: str, tracer: Tracer,
                 max_tokens: int) -> None
    # request(): estimate = tracer.cost_of(ceil(chars(messages)/3), max_tokens); reserve;
    # await wrapped.request; settle with cost_of(usage.input, usage.output, usage.cache_read);
    # on exception settle 0 and re-raise. Calls tracer.check_budget() first.

async def run_agent[D, T](agent: Agent[D, T], prompt: str, ctx: RunContext, *, stage: str,
    name: str, topic_id: str | None = None, model: Model | None = None,
    deps: D | None = None, usage_limits: UsageLimits | None = None,
    ledger: Ledger | None = None, group: str | None = None) -> T
```

  When `ledger` is given, the model is wrapped in `LedgerModel(model, ledger, group or stage, …)`.
  `deps` and `usage_limits` are passed to `agent.run`. `UsageLimitExceeded` is mapped to an
  `EngineError` naming the agent and limit. Existing callers are unchanged.
- Produces (search):
  - `FallbackSearch.search(query, n=5, domains=None, *, depth: Literal["basic","advanced"]="basic")`.
  - `FallbackSearch(providers, tracer, *, ledger: Ledger | None = None, group: str = "scouts", usage: SearchUsage | None = None)`.
    Before each provider call it charges `1 if depth == "basic" else 2` credits to the ledger,
    and records the credits used.
  - `class SearchUsage`: SQLite table `search_usage(provider TEXT, month TEXT, credits INT, PRIMARY KEY(provider, month))`
    in `home.db_path`, with `add(provider, credits)`, `month_total(provider) -> int`.
  - `build_search(ctx, *, ledger=None, group="scouts")`.
  - Tavily sends `search_depth`.

- [ ] **Step 1: Write the failing tests**
  - `test_run_agent_with_deps_and_tools`: an agent with `deps_type=Box` and a tool reading
    `ctx.deps.value`. A `FunctionModel` calls the tool and then outputs; the output contains the
    deps value.
  - `test_run_agent_usage_limit_maps_to_engine_error`: with `UsageLimits(request_limit=1)` and
    a model that loops on tool calls, the run raises `EngineError` whose message contains "limit".
  - `test_ledger_model_reserves_and_settles`: after one call, `ledger.snapshot().groups["planner"].usd_spent > 0`
    and `usd_reserved == 0`.
  - `test_ledger_model_refuses_when_exhausted`: with the planner cap pre-spent, the run raises
    `BudgetExceeded` and the FunctionModel is never invoked (counter == 0).
  - `test_search_charges_credits_and_records_usage`: respx Tavily ok, `depth="advanced"`. Ledger
    scouts credits_spent == 2, and `SearchUsage.month_total("tavily") == 2`.
  - `test_search_refuses_without_credits`: credits exhausted, so `BudgetExceeded` is raised and
    there's no HTTP call (`respx` route not called).
- [ ] **Step 2: Run** `uv run pytest tests/test_llm.py tests/test_search.py -q --no-cov`. Expected: new tests FAIL.
- [ ] **Step 3: Implement.** Check the `WrapperModel.request(messages, model_settings, model_request_parameters)`
  signature on the installed 2.54. `max_tokens` comes from `stage_settings`.
- [ ] **Step 4: Run** the tests, then `make check`. Expected: PASS, with all old llm and search tests green.
- [ ] **Step 5: Commit** `feat(llm): agents with deps, usage limits and per-request budget ledger`.

### Task 4: `engine doctor --agents` (live tool-use smoke test, spec Task 0)

**Files:**
- Modify: `src/kenya_data_engine/cli/doctor.py`, `src/kenya_data_engine/defaults/config.yaml` (stage `research_smoke`)
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `run_agent` (Task 3).
- Produces: `async def _agent_probe(ctx: RunContext, model: Model | None = None) -> tuple[Status, str]`.
  It runs an agent with one tool `add(a: int, b: int) -> int` and output `class Sum(BaseModel): total: int`,
  with the prompt "Use the add tool to add 2 and 3, then report the total."
  - ok when the tool was called and `total == 5`: "tool use + structured output ok";
  - fail with the friendly error otherwise. The fail hint names the two-phase fallback (spec §3).

  `engine doctor --agents` adds this check; plain `doctor` stays unchanged.
- [ ] **Step 1: Write the failing tests:** `test_agent_probe_ok` (a FunctionModel that calls
  `add` then outputs 5), `test_agent_probe_wrong_total_fails`, and
  `test_doctor_agents_flag_lists_check` (CliRunner, `env={"COLUMNS": "200"}`; the output contains
  "agents").
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4:** `make check` PASS.
- [ ] **Step 5: Commit** `feat(doctor): --agents tool-use smoke test`.

### Task 5: URL policy and hardened fetch

**Files:**
- Create: `src/kenya_data_engine/tools/urlpolicy.py`
- Modify: `src/kenya_data_engine/http.py`
- Test: `tests/test_urlpolicy.py`, `tests/test_http.py`

**Interfaces:**
- Produces:

```python
class UnsafeUrl(FetchError): ...
Resolver = Callable[[str], Awaitable[list[str]]]          # host -> IPs; default uses loop.getaddrinfo
async def check_url(url: str, resolve: Resolver | None = None) -> None
    # http/https only; no userinfo; host must resolve; every IP must be global
    # (ipaddress: not private, loopback, link_local, multicast, reserved, unspecified)
def sniff(content: bytes) -> Literal["pdf","xlsx","xls","html","csv","json","unknown"]
    # %PDF- ; PK\x03\x04 + "xl/" in the zip namelist -> xlsx ; D0CF11E0 -> xls ;
    # leading "<" (after whitespace/BOM) -> html ; "{"/"[" -> json ; else csv if it decodes as text

class FetchPolicy(BaseModel): max_bytes: int; max_redirects: int = 5; resolve: Any = None
```

  `http.fetch(..., policy: FetchPolicy | None = None)`. When a policy is given:
  - follow redirects manually (`follow_redirects=False`, up to `max_redirects`), calling
    `check_url` on each hop;
  - stream the body and abort once it exceeds `max_bytes`, raising `FetchError` with "too large";
  - `FetchResult.url` is the final URL.

  Without a policy, behaviour is unchanged.
- [ ] **Step 1: Write the failing tests:**
  - `test_rejects_non_http_schemes` (file, ftp, data);
  - `test_rejects_private_resolution`, with a resolver returning `10.0.0.5`, `127.0.0.1` and
    `169.254.169.254`;
  - `test_accepts_public`, with the resolver returning `41.89.10.10`;
  - `test_redirect_to_private_blocked`: respx 302 to `http://127.0.0.1/x` raises `UnsafeUrl`
    *(Review Focus 2)*;
  - `test_too_many_redirects`;
  - `test_size_cap_aborts`;
  - `test_final_url_after_redirect`;
  - `test_sniff_types`, with tiny byte fixtures built in the test;
  - existing `test_http.py` cases still pass.
- [ ] **Step 2: Run.** FAIL. **Step 3: Implement.** **Step 4:** `make check` PASS.
- [ ] **Step 5: Commit** `feat(http): URL safety policy, checked redirects, size caps`.

### Task 6: Number parsing

**Files:**
- Create: `src/kenya_data_engine/tools/numbers.py`
- Test: `tests/test_numbers.py`

**Interfaces:**
- Produces:

```python
class ParsedNumber(BaseModel):
    value: Decimal; raw: str; unit: Literal["KES","USD","pct","none"]; scale: int  # 10**scale applied
def parse_number(text: str) -> ParsedNumber | None        # one cell/value; None for "-", "–", "n/a", "", ".."
def find_numbers(text: str) -> list[ParsedNumber]          # every number in running text
def same_number(a: ParsedNumber, b: ParsedNumber) -> bool   # equal value and unit after scaling
```

  - **Currency prefixes:** Sh, Shs, KSh, Kshs, KES, US$, $.
  - **Scale words:** thousand, k; million, m, mn; billion, bn, b; trillion, tn.
  - **Percent:** %, "per cent", "percent".
  - **Negatives:** parentheses `(3.2)` are negative, as are a leading minus or a "–" dash
    directly before digits.
  - **Separators:** thousands separators are removed.
- [ ] **Step 1: Write the failing tests** (parametrised):
  - `parse_number("Sh1.2bn").value == Decimal("1200000000")` with unit KES;
  - `"KSh 180.66"` gives 180.66 KES;
  - `"8 per cent"` and `"8%"` give 8 pct;
  - `"1,234.5"` gives 1234.5;
  - `"(3.2)"` gives -3.2;
  - `"–"`, `"n/a"` and `".."` give None;
  - `find_numbers("rose to 19.5% from 9.5%")` gives [19.5, 9.5] *(Review Focus 3)*;
  - `same_number(parse("Sh1.2 billion"), parse("1,200 million shillings"))` is True;
  - `same_number(parse("9.5%"), parse("19.5%"))` is False.
- [ ] **Step 2: Run.** FAIL. **Step 3: Implement.** **Step 4:** `make check` PASS.
- [ ] **Step 5: Commit** `feat(tools): Kenyan number parsing with units and scale`.

### Task 7: Period types

**Files:**
- Create: `src/kenya_data_engine/data/__init__.py`, `src/kenya_data_engine/data/periods.py`
- Test: `tests/test_periods.py`

**Interfaces:**
- Produces:

```python
PeriodType = Literal["day","week","month","quarter","year","fy","epra_cycle"]
class Period(BaseModel, frozen=True):
    type: PeriodType; start: date; end: date; label: str    # label canonical, e.g. "2026-09", "FY2025/26", "EPRA 2026-09-15"
def parse_period(text: str, hint: PeriodType | None = None) -> Period | None
def fy(year_start: int) -> Period                          # FY2025/26 = 2025-07-01..2026-06-30
def epra_cycle(on: date) -> Period                         # cycle containing `on`: 15th..14th next month
NAIROBI = ZoneInfo("Africa/Nairobi")
def today_nairobi(now: datetime | None = None) -> date
```
  Accepted inputs:
  - "Sep 2026", "September 2026", "2026-09", "2026M09";
  - "Q3 2026", "2026Q3";
  - "2026", "FY2025/26", "2025/26", "FY 2025/2026";
  - ISO dates.

  No fuzzy filling from today's date: a missing part means `None`.
- [ ] **Step 1: Write the failing tests:**
  - `fy(2025)` spans 2025-07-01 to 2026-06-30;
  - `parse_period("2025/26").label == "FY2025/26"`;
  - `epra_cycle(date(2026,10,3))` spans 2026-09-15 to 2026-10-14;
  - `parse_period("2026M09")` is month 2026-09;
  - `parse_period("Q3 2026")` spans Jul 1 to Sep 30;
  - `parse_period("garbage")` is None;
  - `parse_period("September")` is None (no year).
- [ ] **Step 2–4:** Run, implement, `make check`. **Step 5: Commit** `feat(data): period types incl. Kenyan FY and EPRA cycle`.

### Task 8: Blob store and series store

**Files:**
- Create: `src/kenya_data_engine/data/models.py`, `src/kenya_data_engine/data/store.py`
- Modify: `src/kenya_data_engine/home.py` (`blobs_dir`, created by `ensure`)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Period` (Task 7).
- Produces:

```python
class Provenance(BaseModel):
    url: str; blob_sha256: str; retrieved_at: datetime; published: date | None
    locator: str          # "p3/t0/r5/c2" (pdf) · "Sheet1!B7" (xlsx) · "t0/r5/c2" (html/csv) · "api:<path>"
    extractor: str        # "pdfplumber@0.11.4", "openpyxl@3.1.5", "worldbank-api@v2"

class Observation(BaseModel):
    series: str; period: Period; entity: str; metric: str; value: Decimal; unit: str
    provenance: Provenance

class StoredObservation(Observation):
    vintage: int; revised: bool   # revised: a different value exists in an earlier vintage

class BlobStore:
    def __init__(self, root: Path) -> None
    def put(self, content: bytes) -> str                   # sha256 hex; idempotent; atomic write
    def path(self, sha: str) -> Path
    def get(self, sha: str) -> bytes
    def prune(self, older_than: timedelta, keep: set[str]) -> int   # keep = blobs referenced by series rows

class SeriesStore:                                          # SQLite WAL in home.db_path
    def __init__(self, db_path: Path) -> None
    def add(self, obs: list[Observation]) -> AddResult      # AddResult(new: int, unchanged: int, revised: int)
    def latest(self, series: str, entity: str | None = None) -> list[StoredObservation]  # newest vintage per key, period order
    def history(self, series: str, period_label: str, entity: str, metric: str) -> list[StoredObservation]
    def series_keys(self) -> list[str]
    def referenced_blobs(self) -> set[str]
```

  The key is `(series, period.label, entity, metric)`. The same value as the latest vintage is
  unchanged (no new row). A different value is a new vintage with `revised=True`. Values are
  stored as TEXT `str(Decimal)`. Table: `observations(series, period_label, period_type, start,
  end, entity, metric, value, unit, vintage, url, blob, retrieved_at, published, locator, extractor)`.
- [ ] **Step 1: Write the failing tests:**
  - `test_blob_put_idempotent`;
  - `test_add_new_then_unchanged` (`AddResult(1,0,0)`, then `(0,1,0)`);
  - `test_revision_keeps_both_vintages`: `latest` returns the new value with `revised=True`,
    and `history` has 2 rows *(Review Focus 5)*;
  - `test_decimal_roundtrip_exact` (`Decimal("180.66")`);
  - `test_prune_keeps_referenced`.
- [ ] **Step 2–4:** Run, implement, `make check`. **Step 5: Commit** `feat(data): content-addressed blobs and vintaged series store`.

### Task 9: Extraction tiers

**Files:**
- Create: `src/kenya_data_engine/data/extract/__init__.py`, `sheets.py`, `html.py`, `pdf.py`
- Modify: `pyproject.toml` (add `openpyxl>=3.1`, `pandas>=2.2`, `pandas-stubs` to dev)
- Test: `tests/test_extract.py`. Fixtures are built in-test: an XLSX via openpyxl, a PDF via
  reportlab (already a dev dependency), HTML and CSV as strings.

**Interfaces:**
- Consumes: `Provenance` (Task 8); `sniff` (Task 5); `DataConfig` (Task 1).
- Produces:

```python
class Locator(BaseModel):            # spec §4; shared with Plan 2b's DataSourceSpec
    pages: list[int] = []; table_index: int = 0; sheet: str | None = None
    css: str | None = None; header_rows: int = 1; columns: dict[str, str] = {}

class RawTable(BaseModel):
    header: list[str]                 # flattened multi-row headers joined with " / "
    rows: list[list[str]]             # cell text, footnote rows removed
    cell_locators: list[list[str]]    # same shape as rows; Provenance.locator strings
    extractor: str; source_kind: Literal["csv","xlsx","html","pdf"]

async def extract_tables(content: bytes, locator: Locator, cfg: DataConfig) -> list[RawTable]
    # dispatch on sniff(); runs parsers via asyncio.to_thread under asyncio.timeout(cfg.parse_timeout_s);
    # raises ExtractError (errors.py) on timeout, unsupported type, page cap, xlsx uncompressed > 10 * cap
def table_to_frame(t: RawTable) -> pandas.DataFrame   # str cells; numbers parsed later via tools.numbers
```

  Rules:
  - **XLSX:** read-only, data_only. Merged header cells are forward-filled.
  - **Header rows:** use `locator.header_rows` when set. Otherwise detect them as leading rows
    where the non-empty cells are mostly non-numeric.
  - **Footnote rows:** a row whose first non-empty cell starts with "Source", "Note", "*" or a
    footnote digit followed by ")" is removed.
  - **Blank columns:** fully blank columns are dropped, and locators keep the original column
    letters.
  - **PDF:** pdfplumber `page.extract_tables()` on `locator.pages` (1-based; all pages up to the
    cap when empty).
  - **HTML:** selectolax `table` elements, or `locator.css`.
- [ ] **Step 1: Write the failing tests:**
  - `test_xlsx_merged_multirow_header`: a 2-row header with a merged "Pump prices" over
    Super/Diesel gives the header `["Town", "Pump prices / Super", "Pump prices / Diesel"]`, and
    `cell_locators[0][1] == "Sheet1!B3"` *(Review Focus 4)*;
  - `test_footnote_rows_dropped`;
  - `test_blank_leading_column_keeps_letters`;
  - `test_csv_thousands_text_cells` (the cell keeps the text "1,234.5");
  - `test_html_table_by_css`;
  - `test_pdf_table_page_locator` (a reportlab table on page 2; the locator starts with "p2/");
  - `test_pdf_page_cap_raises`;
  - `test_xlsx_zip_bomb_guard`: monkeypatch the cap small, giving `ExtractError`;
  - `test_unknown_type_raises`.
- [ ] **Step 2–4:** Run, implement, `make check`. **Step 5: Commit** `feat(data): tiered table extraction with cell provenance`.

### Task 10: Table checks

**Files:**
- Create: `src/kenya_data_engine/data/checks.py`; add `SeriesSpec` and `CheckReport` to `data/models.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Consumes: `Observation`, `SeriesStore.latest` (Task 8).
- Produces:

```python
class SeriesSpec(BaseModel):          # declared per registry series
    key: str; metric: str; unit: str; period_type: PeriodType
    entities: list[str] = []          # expected entities (e.g. towns); empty = any
    min_value: Decimal | None = None; max_value: Decimal | None = None
    max_step_pct: Decimal | None = None      # continuity: |change| vs latest stored value
    totals: list[tuple[str, list[str]]] = [] # (total entity, component entities)
    total_tolerance: Decimal = Decimal("0.5")

class CheckReport(BaseModel):
    status: Literal["accepted", "quarantined"]
    failures: list[str]; warnings: list[str]; checked: int

def check_observations(obs: list[Observation], spec: SeriesSpec,
                       previous: list[StoredObservation]) -> CheckReport
```

  Any of these quarantines the table, and nothing is fixed:
  - unit mismatch;
  - a value outside the range;
  - an expected entity missing (completeness);
  - fewer rows than in `previous` for the same period type;
  - a totals mismatch beyond tolerance;
  - a duplicate key with different values.

  A continuity jump over `max_step_pct` is a **warning** that names the entity and period. It
  needs corroboration in Plan 2b.
- [ ] **Step 1: Write the failing tests:** `test_accepts_clean`, `test_out_of_range_quarantines`,
  `test_missing_entity_quarantines`, `test_totals_reconcile_and_fail`, `test_jump_is_warning`,
  `test_duplicate_conflict_quarantines`.
- [ ] **Step 2–4:** Run, implement, `make check`. **Step 5: Commit** `feat(data): accept-or-quarantine table checks`.

### Task 11: Stats with formula log

**Files:**
- Create: `src/kenya_data_engine/data/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Consumes: `StoredObservation`.
- Produces:

```python
class Figure(BaseModel):
    id: str                       # assigned by FigureBook: "F1", "F2", ...
    label: str; value: Decimal; unit: str; formula: str   # e.g. "(b - a)"
    inputs: list[str]             # input refs: "<series>|<period>|<entity>|<metric>"
class FigureBook:
    def change(self, a: StoredObservation, b: StoredObservation, label: str) -> Figure
    def pct_change(self, a, b, label: str) -> Figure               # ((b-a)/a)*100, unit "pct"
    def yoy(self, obs: list[StoredObservation], label: str) -> Figure  # latest vs same period a year earlier
    def mean(self, obs: list[StoredObservation], label: str) -> Figure
    def real(self, nominal: StoredObservation, cpi_then: StoredObservation,
             cpi_now: StoredObservation, label: str) -> Figure  # nominal * cpi_now / cpi_then; formula names base
    figures: list[Figure]
    def to_markdown(self) -> str                                  # stats.md table: id, label, value, unit, formula, inputs
```
  Decimal arithmetic with context precision 28. Values are rounded only in `to_markdown`
  (2 dp; pct to 1 dp). Mixed units raise `ValueError`. `yoy` raises when no period a year
  earlier exists.
- [ ] **Step 1: Write the failing tests:**
  - `change` of 180.66 → 184.16 is exactly `Decimal("3.50")`, and its formula and inputs are
    recorded;
  - `pct_change` exact;
  - `yoy` across months;
  - `real` with explicit CPI;
  - a units mismatch raises;
  - `to_markdown` contains "F1" and the formula.
- [ ] **Step 2–4:** Run, implement, `make check`. **Step 5: Commit** `feat(data): figures with formula and input log`.

### Task 12: Registry, adapters, catalog

**Files:**
- Create: `src/kenya_data_engine/data/registry.py`, `data/adapters/__init__.py`, `base.py`,
  `worldbank.py`, `sdmx.py`, `listing.py`, `src/kenya_data_engine/defaults/catalog.yaml`
- Modify: `pyproject.toml` (hatch include `defaults/*.yaml` already covers it; check)
- Test: `tests/test_registry.py`, `tests/test_adapters.py`

**Interfaces:**
- Consumes: `fetch` + `FetchPolicy` (Task 5), `BlobStore` and `SeriesStore` (Task 8),
  `extract_tables` (Task 9), `check_observations` (Task 10), `parse_number` (Task 6),
  `parse_period` (Task 7).
- Produces:

```python
class Discovered(BaseModel): url: str; title: str | None; published: date | None
class FetchOutcome(BaseModel):
    key: str; discovered: int; fetched: int; added: AddResult | None
    report: CheckReport | None; error: str | None
class Adapter(Protocol):
    kind: str
    async def discover(self, entry: CatalogEntry, ctx: RunContext) -> list[Discovered]
    async def observations(self, entry: CatalogEntry, item: Discovered, content: bytes,
                           sha: str, ctx: RunContext) -> list[Observation]

class CatalogEntry(_Strict):         # one per series/document in catalog.yaml
    key: str; adapter: Literal["worldbank","sdmx","listing"]; title: str; publisher: str
    tier: Literal[1,2,3,4]; enabled: bool = True; note: str | None = None
    spec: SeriesSpec | None = None   # None for document-only entries (e.g. kenyalaw.gazette)
    params: dict[str, Any] = {}      # adapter-specific (indicator, base_url+flow+key, listing url+link pattern+locator)

def load_catalog(home: EngineHome) -> dict[str, CatalogEntry]      # defaults merged with ~/.kenya-data-engine/catalog.yaml by key
async def fetch_series(key: str, ctx: RunContext, *, limit: int = 12) -> FetchOutcome
    # discover → for each item (newest first, ≤ limit): policy fetch → blob put → observations
    # → check vs store.latest → add if accepted; quarantined: keep blob, record report, add nothing
```

  - **`worldbank`:** `GET https://api.worldbank.org/v2/country/KEN/indicator/{indicator}?format=json&per_page=1000`.
    The response is `[meta, rows]`; each row has `date` ("2023"), `value` (number or null) and
    `indicator.value`. Null values are skipped. Values become `Decimal(str(v))`. The period type
    is year. The locator is `api:/v2/country/KEN/indicator/{indicator}#{date}`.
  - **`sdmx`:** SDMX-JSON 2.1 `GET {base_url}/data/{flow}/{key}?startPeriod=…`.
    - Observations come from `data.dataSets[0].series[*].observations`, with
      `structure.dimensions.observation[0].values[i].id` as the period.
    - `base_url`, `flow`, `key`, `unit` and `metric` live in `params`.
    - The adapter is generic; the IMF endpoint gets verified by the probe.
  - **`listing`:**
    - Crawl `params.url` and collect `<a href>` links that match `params.link_pattern` (a
      regex), absolutised.
    - `published` comes from `params.date_pattern` applied to the link text or URL via
      `parse_period`.
    - For series entries, extract with `params.locator`, then map columns with
      `params.columns` (`{source header: "entity" | "value:<metric>" | "period"}`). Values go
      through `parse_number`.
    - Document-only entries (no spec) only discover.
  - **`catalog.yaml` defaults:**
    - **`wb:FP.CPI.TOTL.ZG`:** inflation, annual %, tier 1, enabled.
    - **`wb:PA.NUS.FCRF`:** official KES/USD rate, tier 1, enabled.
    - **`imf:cpi`:** sdmx; base_url is the IMF portal URL from the catalog research doc. Disabled,
      with the note "endpoint unverified; run engine catalog probe".
    - **`epra.pump_prices`, `cbk.weekly_bulletin`, `knbs.cpi`, `cma.bulletins`, `treasury.qebr`,
      `kenyalaw.gazette`:** listing entries using the candidate listing URLs and link patterns from
      `docs/research/2026-10-09-kenya-data-catalog.md`. **Disabled**, with the note "unverified;
      parser pending real samples". There is no spec (columns are unknown) except the EPRA spec
      skeleton (metric pump_price, unit KES, period epra_cycle, range 50–500, max_step_pct 15).
- [ ] **Step 1: Write the failing tests:**
  - `test_catalog_loads_and_merges_by_key`;
  - `test_unknown_adapter_rejected`;
  - `test_worldbank_fetch_series`: respx JSON with 3 rows, one null, gives `AddResult(new=2)`,
    and the locator contains the date;
  - `test_sdmx_parse`: a minimal SDMX-JSON fixture gives correct periods and values;
  - `test_listing_discover_pattern_and_dates`;
  - `test_listing_series_extract_maps_columns`: an XLSX fixture behind a listing page gives
    observations with entity and value from the columns map;
  - `test_quarantine_adds_nothing_keeps_blob`;
  - `test_disabled_entry_refused` (a clear error that mentions the note).
- [ ] **Step 2–4:** Run, implement, `make check`. **Step 5: Commit** `feat(data): series registry with World Bank, SDMX and listing adapters`.

### Task 13: Commands: `engine data`, `engine catalog probe`, `engine gc`

**Files:**
- Create: `src/kenya_data_engine/cli/data.py`, `cli/catalog.py`, `cli/gc.py`
- Modify: `src/kenya_data_engine/cli/app.py` (register; add the examples to the epilog),
  `README.md` (a short section)
- Test: `tests/test_cli_data.py`

**Interfaces:**
- Consumes: `fetch_series`, `load_catalog`, `SeriesStore`, `BlobStore`.
- Produces:
  - `engine data list`: catalog keys, title, adapter, tier, enabled, rows stored.
  - `engine data fetch <key> [--limit N] [--json]`: runs `fetch_series`. Prints a Rich summary
    (discovered, fetched, new, unchanged, revised, and check status with its failures). Exit 1 on
    error or quarantine.
  - `engine data show <key> [--entity E] [--last N] [--csv]`: a latest-vintage table with
    period, entity, metric, value, unit, a revised mark and the source host. `--csv` writes to
    stdout.
  - `engine catalog probe [KEY…] [--all] [--save-samples DIR] [--json]`: for each entry
    (disabled ones included; probing is how they get enabled), runs `discover`. It reports the
    status, final URL, links found (count plus the first 3) and the sniffed type of the first
    item. With `--save-samples`, it saves the first item's bytes to
    `DIR/<key>/<filename>`, at most 2 files per key and at most 10 MB each. It also writes
    `DIR/probe.json`. No series rows are written.
  - `engine gc [--older-than 90d] [--yes]`: prunes blobs not referenced by the series store,
    reports the count and the bytes freed, and asks for confirmation without `--yes`.
- [ ] **Step 1: Write the failing tests** (CliRunner, respx, `env={"COLUMNS": "200"}`):
  - `test_data_fetch_and_show_worldbank`;
  - `test_data_show_csv`;
  - `test_catalog_probe_reports_and_saves_samples` (writes files and `probe.json`; series store
    still empty);
  - `test_catalog_probe_handles_404` (row status error; exit 0, since the probe is diagnostic);
  - `test_gc_prunes_unreferenced`.
- [ ] **Step 2–4:** Run, implement, `make check`. **Step 5: Commit** `feat(cli): engine data, catalog probe and gc`.

### Task 14 (blocked on the user): EPRA, CBK and KNBS parsers from real samples

Not executed in this batch. After the user runs
`engine catalog probe --all --save-samples tests/fixtures/real` in a clone and pushes the samples,
a follow-up plan task writes each entry's `params` (link pattern, locator, columns map), the
`SeriesSpec`, and tests against the real files, then enables the entries.

---

## Execution order and batching
- **Batch A:** Tasks 1 → 2 → 3 → 4. These are sequential: they touch config, llm and search.
- **Batch B:** Tasks 5, 6 and 7. These are independent and can run in parallel.
- **Batch C:** Tasks 8 → 9 → 10 → 11.
- **Batch D:** Tasks 12 → 13.
- Then the user does a laptop check: `engine doctor --agents`, `engine data fetch wb:FP.CPI.TOTL.ZG`,
  and `engine catalog probe --all --save-samples …`.
