# Kenya Data Engine — Plan 1: Foundation, Radar, Synthesizer, CLI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pip/uv-installable `engine` CLI. `engine run` collects Kenyan signals (Radar), clusters and scores them with DeepSeek (Synthesizer), and prints a ranked topic list. Every stage can run on its own, the pipeline can resume, and runs are traced and budgeted.

**Architecture:**
- A `src/` layout Python package, `kenya_data_engine`.
- Stages are plain classes with typed Pydantic input and output, persisted as JSON in `runs/<run-id>/`, and chained by a small async orchestrator.
- Agents use Pydantic AI over DeepSeek's OpenAI-compatible API.
- Tools and adapters are plain async functions that share one `RunContext` (HTTP client, cache, tracer, config).

**Tech Stack:**
- Python ≥3.12, uv
- Typer, Rich
- Pydantic v2, pydantic-settings
- Pydantic AI
- httpx, tenacity
- feedparser, selectolax, trafilatura, pdfplumber, python-dateutil
- rapidfuzz, PyYAML, SQLite (stdlib)
- pytest, pytest-asyncio, pytest-cov, respx
- ruff, mypy, pre-commit, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-10-09-kenya-data-engine-design.md`

**Out of scope (Plans 2–3):** Planner, Data Scout, Dossier, Memory tables (except `cache`), `report`, `eval`, `research`, the live dashboard, the `browse` TUI, Docling.

## Global Constraints

- Python `>=3.12`. Package `kenya_data_engine`, console script `engine`, repo `kenya-data-engine` (private).
- Default home `~/.kenya-data-engine/`, overridable with the `ENGINE_HOME` env var. It holds `config.yaml`, `calendar.yaml`, `.env`, `engine.db`, `runs/`, `briefs/`.
- Run id format is `YYYY-MM-DD-HHMM`. On a collision in the same minute, append `-2`, `-3`, and so on.
- LLM: model `deepseek-flash`, base URL `https://api.deepseek.com`. Pricing defaults are $0.15 per million input tokens and $0.60 per million output tokens. The run budget default is $0.50.
- Scoring weights (default): `data_ability 0.35, wallet_impact 0.20, timeliness 0.20, clarity_gap 0.15, novelty 0.10`. They must sum to 1.0. `top_n` defaults to 5.
- **No number from an LLM.** In this plan, the LLM produces only clusters, rubric scores of 1–5, and text. `final_score` is computed in code.
- Secrets live only in `<home>/.env` (mode 600) or the environment. They are never written to traces, artifacts or logs.
- Tests never touch the network. HTTP uses `respx`, and LLMs use Pydantic AI `FunctionModel`/`TestModel`. Live checks belong only in `engine doctor` and the spikes.
- User-facing errors are raised as `EngineError(message, hint)`. The CLI shows message and hint, with a traceback only under `--verbose`.
- `make check` (ruff lint, ruff format --check, mypy, pytest with coverage ≥85%) must pass at the end of every task.
- Commit messages follow Conventional Commits (`feat:`, `test:`, `chore:`, `docs:`).

## Review Focus

1. **Missing or invalid API key.** `engine run` and `engine doctor` print a one-line error with the hint "run `engine init`" and exit with code 2, with no traceback. Owned by Tasks 4 and 15.
2. **One source is down, times out or returns garbage HTML.** The Radar still returns the other sources' signals and lists the failure in `RadarResult.errors`. Owned by Task 11.
3. **Malformed, empty or non-UTF-8 RSS feed.** It yields zero signals, raises no exception, and is not counted as an adapter error unless parsing raised. Owned by Task 11.
4. **The LLM returns cluster signal ids that don't exist, or empty clusters.** Unknown ids are dropped, and clusters left empty are dropped and recorded in `TopicList.dropped`. Owned by Task 13.
5. **Resume over a corrupted or partial artifact** (truncated JSON from a crash). The artifact is treated as missing and the stage re-runs. Owned by Task 8.

---

## File Structure

```
kenya-data-engine/
  pyproject.toml  Makefile  README.md  .gitignore  .env.example  .pre-commit-config.yaml
  .github/workflows/ci.yml
  docs/superpowers/specs/…  docs/superpowers/plans/…
  spikes/                         throwaway; not packaged
    RESULTS.md
  src/kenya_data_engine/
    __init__.py                   __version__
    errors.py                     EngineError, ConfigError, BudgetExceeded, FetchError, SearchError
    home.py                       EngineHome (paths)
    config.py                     Secrets, EngineConfig, load_config, load_secrets
    defaults/config.yaml          packaged defaults
    defaults/calendar.yaml
    models.py                     Signal, AdapterError, RadarResult, TopicScores, Topic, TopicList
    runs.py                       RunStore, RunHandle
    trace.py                      TraceEvent, Tracer
    cache.py                      Cache, CacheEntry
    http.py                       FetchResult, fetch
    context.py                    RunContext, open_context
    pipeline.py                   Stage protocol, run_pipeline
    llm.py                        build_model, stage_settings, run_agent (usage → tracer)
    prompts/__init__.py           load_prompt(name)
    prompts/cluster.md  prompts/score.md
    tools/search.py               SearchResult, SearchProvider, Tavily, Serper, FallbackSearch, build_search
    tools/fetch.py                PageText, fetch_page
    tools/pdf.py                  PdfPage, PdfText, read_pdf
    tools/grounding.py            normalize, quote_in_text
    radar/base.py                 Adapter protocol, dedupe, run_radar, RadarStage, build_adapters
    radar/rss.py                  RssAdapter (news + Google Trends KE)
    radar/calendar.py             CalendarAdapter, occurrences
    radar/listing.py              ListingAdapter (CBK, KNBS, EPRA, Parliament)
    synth/cluster.py              Cluster, ClusterOutput, cluster_signals
    synth/score.py                ScoreOutput, weighted_score, rank_topics, score_cluster
    synth/stage.py                SynthesizeStage
    cli/app.py                    Typer root, global options, error handler
    cli/ui.py                     console, theme, render helpers
    cli/run.py  cli/stage.py  cli/init.py  cli/doctor.py
  tests/
    conftest.py                   tmp home, ctx fixture, fake model helpers
    fixtures/rss/  fixtures/listing/  fixtures/pdf/
    test_*.py                     mirrors modules
```

---

### Task 1: Project scaffold and quality gates

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.gitignore`, `.env.example`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `README.md`
- Create: `src/kenya_data_engine/__init__.py`, `src/kenya_data_engine/cli/app.py`
- Copy: the spec and this plan into `docs/superpowers/` of the new repo
- Test: `tests/test_cli_smoke.py`

**Interfaces:**
- Produces: `kenya_data_engine.__version__: str = "0.1.0"`, and `cli.app.app: typer.Typer`, wired to the console script `engine`.

- [ ] **Step 1: Write the failing test**

```python
from typer.testing import CliRunner
from kenya_data_engine.cli.app import app

def test_version_flag():
    r = CliRunner().invoke(app, ["--version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.stdout
```

- [ ] **Step 2: Create `pyproject.toml`.**
  - Hatchling build, with `[project.scripts] engine = "kenya_data_engine.cli.app:app"`.
  - Runtime deps from the Tech Stack line.
  - Dev deps in `[dependency-groups] dev`.
  - ruff config with `line-length = 100` and rules `E,F,I,UP,B,SIM,RUF`.
  - mypy with `strict = true` for `kenya_data_engine.*`, and pydantic plugin.
  - pytest with `asyncio_mode = "auto"` and `addopts = "--cov=kenya_data_engine --cov-fail-under=85"`.
  - Include `defaults/*.yaml` and `prompts/*.md` as package data.

- [ ] **Step 3: Implement the `app` callback with `--version` (eager option) in `cli/app.py`.**

- [ ] **Step 4: Add the `Makefile` targets.**
  - `install` (`uv sync`), `fmt` (`ruff format . && ruff check --fix .`), `lint`, `typecheck`, `test`, `eval` (placeholder that prints "Plan 3"), and `check` (`lint` + format check + `typecheck` + `test`).
  - Add the CI workflow: `astral-sh/setup-uv`, `uv sync`, `make check` on push and PR.
  - Add pre-commit (ruff and ruff-format hooks).
  - `.gitignore`: `.env`, `.venv`, `__pycache__`, `.coverage`, `runs/`, `briefs/`, `*.db`.
  - `.env.example` lists `DEEPSEEK_API_KEY=`, `TAVILY_API_KEY=`, `SERPER_API_KEY=`, `JINA_API_KEY=`.

- [ ] **Step 5: Write the `README.md`.** Include a one-line pitch, install (`uv tool install git+https://github.com/<owner>/kenya-data-engine`), quickstart (`engine init`, `engine doctor`, `engine run`) and the dev setup (`uv sync`, `make check`).

- [ ] **Step 6: Verify.** Run `uv sync && make check`. Expected: test passes and lint/typecheck are clean. The coverage gate may need `--cov-fail-under` set to 0 for this task only, restored to 85 in Task 4.

- [ ] **Step 7: Commit** with `chore: scaffold project, quality gates and CI`.

---

### Task 2: Spike: DeepSeek and Pydantic AI (throwaway, needs `DEEPSEEK_API_KEY`)

**Files:**
- Create: `spikes/deepseek_pydantic_ai.py`, `spikes/RESULTS.md`

**Interfaces:**
- Produces: a decision record in `spikes/RESULTS.md` that Task 13 reads. It records the Pydantic AI model and provider class names, the `extra_body` keys that control thinking and reasoning effort, whether structured output works with thinking on, and the observed token usage fields.

- [ ] **Step 1: Write the spike script.** It builds a Pydantic AI `Agent` against `deepseek-flash` and runs four probes:
  - (a) plain text output
  - (b) `output_type` set to a 3-field Pydantic model
  - (c) one tool call to a trivial `add(a, b)` tool
  - (d) (b) repeated with thinking on and at two reasoning-effort levels, using the `extra_body` keys from DeepSeek's API docs

  Print `result.usage()` for each probe.

- [ ] **Step 2: Run it.** `uv run python spikes/deepseek_pydantic_ai.py`. Expected: all 4 probes print outputs and usage. Record failures verbatim.

- [ ] **Step 3: Write the findings in `spikes/RESULTS.md`, under "Spike 1".** Record the class names, the working `extra_body` per effort level, structured-output reliability, and the latency and tokens observed.
  - If structured output fails with thinking on, record the decision **"thinking off for structured stages"**.
  - If Pydantic AI can't talk to DeepSeek at all, stop and raise it with the human before continuing.

- [ ] **Step 4: Commit** with `chore(spike): DeepSeek x Pydantic AI findings`.

---

### Task 3: Spike: source access (throwaway)

**Files:**
- Create: `spikes/sources.py`; append to `spikes/RESULTS.md`
- Create: `tests/fixtures/rss/<name>.xml` and `tests/fixtures/listing/<name>.html` (captured)

**Interfaces:**
- Produces: for each source, a verified URL and CSS selectors, recorded in `RESULTS.md` under "Spike 3", and the captured fixtures used by Tasks 11 and 12.

- [ ] **Step 1: Probe the RSS sources.**
  - Find and verify RSS URLs for Business Daily, Nation, The Standard, The Star, Capital FM Business, Kenyans.co.ke, and Google Trends KE (`https://trends.google.com/trending/rss?geo=KE`).
  - For each one: HTTP status, entry count, whether entries have dates.
  - Save one response per feed to `tests/fixtures/rss/`.

- [ ] **Step 2: Probe the listing pages.**
  - Pick one listing page each for CBK (MPC press releases or the publications list), KNBS (CPI releases), EPRA (pump prices) and Parliament (bills).
  - Record the URL, the CSS selector for items, and the selector or attribute for title, link and date (if present).
  - Save each page to `tests/fixtures/listing/`.

- [ ] **Step 3: Write the findings.** Sources that need JavaScript, block bots, or offer only PDFs are marked `deferred` with the reason.

- [ ] **Step 4: Commit** with `chore(spike): source access findings and fixtures`.

---

### Task 4: Errors, home and config

**Files:**
- Create: `src/kenya_data_engine/errors.py`, `home.py`, `config.py`, `defaults/config.yaml`, `defaults/calendar.yaml`
- Test: `tests/test_config.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: Spike 3 URLs and selectors (filled into `defaults/config.yaml`).
- Produces:
  - `EngineError(message: str, hint: str | None = None)`, and subclasses `ConfigError`, `BudgetExceeded`, `FetchError`, `SearchError`.
  - `EngineHome(root: Path)` with properties `config_path`, `calendar_path`, `env_path`, `db_path`, `runs_dir`, `briefs_dir`, and the method `ensure() -> None` (creates the directories). `EngineHome.resolve(explicit: Path | None = None) -> EngineHome` resolves in this order: `explicit`, then `ENGINE_HOME`, then `~/.kenya-data-engine`.
  - `Secrets` (pydantic-settings), with fields `deepseek_api_key`, `tavily_api_key`, `serper_api_key` and `jina_api_key`, all `SecretStr | None`. `load_secrets(home) -> Secrets` reads `home.env_path` and the environment; the environment wins.
  - `EngineConfig` with nested models:
    - `top_n: int = 5`, `concurrency: int = 4`
    - `weights: dict[str, float]`
    - `llm: LLMConfig` containing `base_url`, `pricing` (`input_per_m`, `output_per_m`), and `stages: dict[str, StageLLM]`. `StageLLM` has `model: str`, `max_tokens: int`, `extra_body: dict[str, Any]`.
    - `budgets: Budgets`, containing `run_usd: float = 0.50`
    - `search: SearchConfig`, containing `providers: list[str] = ["tavily", "serper"]`
    - `radar: RadarConfig`, containing `since_hours: int = 72`, `lookahead_days: int = 21`, `max_items: int = 10`, `feeds: dict[str, str]`, `trends_feed: str`, `listings: dict[str, ListingSpec]`, `enabled: list[str]`. `ListingSpec` has `url`, `item`, `title`, `link`, `date: str | None`, `kind`.
    - `cache_ttl_hours: float = 6.0`
    - `synth: SynthConfig`, containing `max_signals: int = 300`
  - `load_config(home: EngineHome) -> EngineConfig`. It deep-merges the user's `config.yaml` over the packaged defaults and validates. If the weights don't sum to 1.0 (within ±1e-6), it raises `ConfigError("weights must sum to 1.0", hint=...)`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_home_resolution_prefers_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_HOME", str(tmp_path))
    assert EngineHome.resolve().root == tmp_path

def test_defaults_load_without_user_file(tmp_home):
    cfg = load_config(tmp_home)
    assert cfg.top_n == 5 and cfg.budgets.run_usd == 0.50
    assert cfg.weights == {"data_ability": 0.35, "wallet_impact": 0.20,
                           "timeliness": 0.20, "clarity_gap": 0.15, "novelty": 0.10}
    assert cfg.llm.stages["synthesize_cluster"].model == "deepseek-flash"
    assert cfg.llm.stages["synthesize_score"].model == "deepseek-flash"

def test_user_yaml_overrides_one_key_keeps_rest(tmp_home):
    tmp_home.config_path.write_text("top_n: 8\n")
    cfg = load_config(tmp_home)
    assert cfg.top_n == 8 and cfg.budgets.run_usd == 0.50

def test_bad_weights_raise_config_error(tmp_home):
    tmp_home.config_path.write_text("weights: {data_ability: 0.9, wallet_impact: 0.9, timeliness: 0, clarity_gap: 0, novelty: 0}\n")
    with pytest.raises(ConfigError):
        load_config(tmp_home)

def test_env_secret_overrides_dotenv(tmp_home, monkeypatch):
    tmp_home.env_path.write_text("DEEPSEEK_API_KEY=fromfile\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fromenv")
    assert load_secrets(tmp_home).deepseek_api_key.get_secret_value() == "fromenv"
```

The `tmp_home` fixture in `conftest.py` returns `EngineHome(tmp_path)` after `ensure()`, with `ENGINE_HOME` and all `*_API_KEY` env vars removed.

- [ ] **Step 2: Run** `uv run pytest tests/test_config.py -v`. Expected: FAIL (import errors).

- [ ] **Step 3: Implement the modules and the defaults.**
  - **`defaults/config.yaml`:** the values from Global Constraints; feeds, listings and trends from Spike 3; stage entries `synthesize_cluster` and `synthesize_score` with `extra_body` from Spike 1 at low effort; `enabled` lists every non-deferred adapter.
  - **`defaults/calendar.yaml`** entries (`rule` is one of `monthly` with `day` from 1–28 or −1 for the last day, `annual` with `month` and `day`, or `date` with an ISO `date`):
    - EPRA pump price review: monthly, day 14, personal_finance.
    - KNBS CPI release: monthly, day −1, economy.
    - School fees season: annual, 1 Jan, personal_finance.
    - Finance Bill season: annual, 1 May, law.
    - Budget reading: annual, 12 Jun, economy, with the note "approximate — confirm date".
    - Festive spending: annual, 1 Dec, personal_finance.
    - A commented example of a `date` entry for MPC meetings.

- [ ] **Step 4: Run the tests.** Expected: PASS. Restore `--cov-fail-under=85`.

- [ ] **Step 5: Commit** with `feat: engine home, config and secrets`.

---

### Task 5: Models and run store

**Files:**
- Create: `src/kenya_data_engine/models.py`, `runs.py`
- Test: `tests/test_models.py`, `tests/test_runs.py`

**Interfaces:**
- Produces:
  - `SignalKind = Literal["news", "data_release", "policy", "attention", "calendar"]`, `Category = Literal["economy", "personal_finance", "startups", "business", "law"]`.
  - `signal_id(url: str | None, title: str) -> str`: the first 12 hex characters of sha1 over the normalized URL if present, else over `"title:" + casefolded title`.
  - `normalize_url(url: str) -> str`: lowercases the host, drops the fragment and `utm_*` params, and strips a trailing `/`.
  - `Signal(id, kind, title, source, url: str | None, published_at: datetime | None, snippet: str = "", meta: dict[str, str] = {})`.
  - `AdapterError(adapter: str, message: str)`, `RadarResult(signals: list[Signal], errors: list[AdapterError], collected_at: datetime)`.
  - `TopicScores(data_ability, wallet_impact, timeliness, clarity_gap, novelty: int  # 1..5, justification: dict[str, str])`.
  - `Topic(id, title, summary, why_now, category: Category, signal_ids: list[str], scores: TopicScores, final_score: float)`.
  - `TopicList(topics: list[Topic], dropped: list[str] = [], budget_exhausted: bool = False)`.
  - `RunStore(runs_dir: Path)` with `new_run(now: datetime) -> RunHandle`, `open(run_id: str) -> RunHandle` (raises `EngineError` if it doesn't exist), `latest() -> RunHandle | None` and `list() -> list[str]` (newest first).
  - `RunHandle(run_id: str, dir: Path)` with `write(name: str, model: BaseModel) -> Path` (atomic: write `.tmp` then rename, to `<dir>/<name>.json`), `read(name: str, type_: type[T]) -> T | None` (returns `None` if the file is missing **or** fails JSON or validation) and `trace_path` (`<dir>/trace.jsonl`).

- [ ] **Step 1: Write the failing tests.**

```python
def test_signal_id_stable_across_tracking_params():
    a = signal_id("https://X.com/a/?utm_source=t#top", "T")
    assert a == signal_id("https://x.com/a", "other title") and len(a) == 12

def test_scores_reject_out_of_range():
    with pytest.raises(ValidationError):
        TopicScores(data_ability=6, wallet_impact=3, timeliness=3, clarity_gap=3, novelty=3, justification={})

def test_run_id_format_and_collision(tmp_path):
    s = RunStore(tmp_path); now = datetime(2026, 10, 9, 14, 5)
    assert s.new_run(now).run_id == "2026-10-09-1405"
    assert s.new_run(now).run_id == "2026-10-09-1405-2"

def test_read_returns_none_for_truncated_json(tmp_path):   # Review Focus 5
    h = RunStore(tmp_path).new_run(datetime(2026, 10, 9, 14, 5))
    (h.dir / "signals.json").write_text('{"signals": [')
    assert h.read("signals", RadarResult) is None

def test_write_read_roundtrip(tmp_path): ...  # RadarResult with one Signal survives a round trip unchanged
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** `models.py` and `runs.py` per the Interfaces.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: core models and run store`.

---

### Task 6: Tracer and budgets

**Files:**
- Create: `src/kenya_data_engine/trace.py`
- Test: `tests/test_trace.py`

**Interfaces:**
- Produces:
  - `TraceEvent(ts: datetime, run_id: str, stage: str, kind: Literal["llm", "tool", "http", "stage"], name: str, status: Literal["ok", "error"], latency_ms: int, topic_id: str | None = None, input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0, error: str | None = None, attrs: dict[str, Any] = {})`.
  - `Tracer(path: Path, run_id: str, input_per_m: float, output_per_m: float, run_budget_usd: float)` with:
    - `record(event: TraceEvent) -> None`, which appends one JSON line and flushes.
    - `record_llm(stage, name, input_tokens, output_tokens, latency_ms, topic_id=None, status="ok", error=None) -> float`, which computes cost, records the event, and returns the cost.
    - `span(stage, kind, name, topic_id=None)`, an async context manager that times the block and records ok, or error with the exception message, then re-raises.
    - `total_cost: float`.
    - `remaining_usd: float`.
    - `check_budget() -> None`, which raises `BudgetExceeded("run budget of $X exhausted", hint="raise budgets.run_usd in config.yaml")` when `total_cost >= run_budget_usd`.
    - `summary() -> dict`, with counts and cost per stage and the error count.

- [ ] **Step 1: Write the failing tests.**

```python
def test_llm_cost_uses_pricing(tmp_path):
    t = Tracer(tmp_path/"t.jsonl", "r", 0.15, 0.60, 0.50)
    assert t.record_llm("synthesize", "cluster", 1_000_000, 1_000_000, 10) == pytest.approx(0.75)

def test_budget_exceeded(tmp_path):
    t = Tracer(tmp_path/"t.jsonl", "r", 0.15, 0.60, 0.10)
    t.record_llm("s", "n", 1_000_000, 0, 1)
    with pytest.raises(BudgetExceeded): t.check_budget()

async def test_span_records_error_and_reraises(tmp_path):
    t = Tracer(tmp_path/"t.jsonl", "r", 0.15, 0.60, 0.50)
    with pytest.raises(ValueError):
        async with t.span("radar", "tool", "x"): raise ValueError("boom")
    line = json.loads((tmp_path/"t.jsonl").read_text().splitlines()[-1])
    assert line["status"] == "error" and line["error"] == "boom"

def test_no_secret_in_trace(tmp_path): ...  # record attrs={"api_key": "sk-123"} → the written line has "***" in place of it (redact keys matching /key|token|secret/i)
```

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: JSONL tracer with cost accounting and budgets`.

---

### Task 7: Cache and HTTP fetch

**Files:**
- Create: `src/kenya_data_engine/cache.py`, `http.py`
- Test: `tests/test_cache.py`, `tests/test_http.py`

**Interfaces:**
- Produces:
  - `CacheEntry(key, content: bytes, content_type: str, fetched_at: datetime)`.
  - `Cache(db_path: Path)`. It creates the table `cache(key TEXT PRIMARY KEY, content BLOB, content_type TEXT, fetched_at TEXT, expires_at TEXT)` and has `get(key, now: datetime | None = None) -> CacheEntry | None` (expired entries count as missing) and `put(key, content, content_type, ttl_hours) -> None`.
  - `FetchResult(url, status: int, content: bytes, content_type: str, from_cache: bool)`.
  - `async fetch(url: str, *, client: httpx.AsyncClient, cache: Cache, ttl_hours: float, headers: dict | None = None) -> FetchResult`:
    - The cache key is `normalize_url(url)`.
    - Uses a 20 s timeout.
    - Retries with tenacity: 3 attempts, exponential backoff (0.5 s, 1 s, 2 s) with jitter, on `httpx.TransportError` or status ≥500.
    - On 4xx, or when retries are exhausted, raises `FetchError(f"{status} for {url}")`.
    - Sends a `User-Agent` of `kenya-data-engine/<version> (+research)`.
    - Caches only 2xx responses.

- [ ] **Step 1: Write the failing tests** (respx).

```python
async def test_second_fetch_hits_cache(respx_mock, cache):
    route = respx_mock.get("https://a.ke/x").respond(200, text="hi")
    async with httpx.AsyncClient() as c:
        await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
        r = await fetch("https://a.ke/x", client=c, cache=cache, ttl_hours=1)
    assert r.from_cache and route.call_count == 1

async def test_retries_5xx_then_succeeds(respx_mock, cache): ...  # responses 503, 503, 200 → status 200 with call_count 3
async def test_404_raises_fetch_error_without_retry(respx_mock, cache): ...  # call_count 1
def test_expired_entry_is_miss(cache): ...  # put with ttl 1h, get at now+2h → None
```

Patch the tenacity wait to zero in tests via a fixture.

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: sqlite cache and retrying fetch`.

---

### Task 8: Run context and pipeline orchestrator

**Files:**
- Create: `src/kenya_data_engine/context.py`, `pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `EngineHome`, `load_config`, `load_secrets` (Task 4); `RunStore` and `RunHandle` (Task 5); `Tracer` (Task 6); `Cache` (Task 7).
- Produces:
  - `StageEvent(stage: str, status: Literal["start", "skip", "done", "error"], detail: str = "")`.
  - `@dataclass RunContext` with `home`, `config`, `secrets`, `run: RunHandle`, `tracer`, `cache`, `http: httpx.AsyncClient`, and `emit: Callable[[StageEvent], None]` (default no-op).
  - `@asynccontextmanager async def open_context(home: EngineHome, run: RunHandle, emit=None) -> AsyncIterator[RunContext]` owns the httpx client's lifecycle.
  - `class Stage(Protocol[I, O])` with `name: str`, `output_name: str`, `output_type: type[O]`, `async def run(self, ctx: RunContext, inp: I) -> O`.
  - `async run_pipeline(stages: Sequence[Stage[Any, Any]], ctx: RunContext, *, resume: bool = False) -> BaseModel`:
    - Feeds each output to the next stage; the first stage gets `None`.
    - With `resume`, a stage whose artifact `ctx.run.read(output_name, output_type)` is not `None` is skipped (emits `skip`) and its artifact is used.
    - Writes each output via `ctx.run.write`.
    - Wraps each stage in a tracer span (`kind="stage"`).
    - Emits start, done or error.
    - Writes `summary.json` (from `tracer.summary()`) on completion **and** on failure.
    - Re-raises the stage's exception after emitting the error event.

- [ ] **Step 1: Write the failing tests**, using two fake stages that return small models and count their calls.

```python
async def test_chains_outputs_and_writes_artifacts(ctx): ...  # second stage receives the first's output; both .json files exist
async def test_resume_skips_completed_stage(ctx): ...         # pre-write a valid A artifact → A.calls == 0, B.calls == 1
async def test_resume_reruns_stage_with_corrupt_artifact(ctx): # Review Focus 5
    (ctx.run.dir / "a.json").write_text("{bad"); await run_pipeline([A(), B()], ctx, resume=True)
    assert A.calls == 1
async def test_failure_emits_error_and_writes_summary(ctx): ...  # B raises → re-raised; events end with ("b", "error"); summary.json exists
```

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: run context and resumable pipeline orchestrator`.

---

### Task 9: Search tool

**Files:**
- Create: `src/kenya_data_engine/tools/__init__.py`, `tools/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `RunContext` (Task 8).
- Produces:
  - `SearchResult(title: str, url: str, snippet: str, content: str | None = None)`.
  - `class SearchProvider(Protocol)` with `name: str` and `async search(query: str, n: int = 5, domains: list[str] | None = None) -> list[SearchResult]`.
  - `TavilyProvider(api_key: str, client)`: `POST https://api.tavily.com/search` with JSON `{"query", "max_results": n, "include_domains": domains}` and header `Authorization: Bearer <key>`. It maps `results[].{title, url, content}` to `snippet`.
  - `SerperProvider(api_key, client)`: `POST https://google.serper.dev/search` with header `X-API-KEY` and JSON `{"q", "num": n}`. Domains are added to `q` as `(site:a OR site:b)`, and `organic[].{title, link, snippet}` is mapped.
  - `FallbackSearch(providers: list[SearchProvider], tracer)`, which itself implements `SearchProvider` and is named `"fallback"`. It tries the providers in order, records a `tool` span for each attempt, and moves on to the next provider on any exception. If all fail, it raises `SearchError("all search providers failed", hint="check keys with engine doctor")`.
  - `build_search(ctx: RunContext) -> FallbackSearch` keeps the providers from `config.search.providers` that have a key. If none have one, it raises `ConfigError("no search provider key configured", hint="run engine init")`.

- [ ] **Step 1: Write the failing tests** (respx).

```python
async def test_tavily_maps_results(respx_mock, ctx): ...      # one result → SearchResult fields mapped
async def test_serper_adds_site_filter(respx_mock, ctx): ...  # the request body's q contains "(site:cbk.go.ke)"
async def test_fallback_uses_second_on_first_error(respx_mock, ctx): ...  # tavily 500 → serper results returned
async def test_all_fail_raises_search_error(respx_mock, ctx): ...
def test_build_search_without_keys_raises_config_error(ctx_no_keys): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: pluggable search with Tavily, Serper and fallback`.

---

### Task 10: Page, PDF and grounding tools

**Files:**
- Create: `src/kenya_data_engine/tools/fetch.py`, `tools/pdf.py`, `tools/grounding.py`
- Create: `tests/fixtures/pdf/sample_table.pdf` (generated in the test setup with reportlab, or a tiny committed PDF with a 2×3 table)
- Test: `tests/test_tools_fetch.py`, `tests/test_tools_pdf.py`, `tests/test_grounding.py`

**Interfaces:**
- Consumes: `fetch` (Task 7), `RunContext` (Task 8).
- Produces:
  - `PageText(url, title: str | None, text: str, fetched_at: datetime, via: Literal["direct", "jina"])`.
  - `async fetch_page(url: str, ctx: RunContext) -> PageText`:
    - Uses `fetch` and then `trafilatura.extract(html, include_tables=True)`.
    - Falls back to Jina when the extracted text has fewer than 200 characters: `GET https://r.jina.ai/<url>`, with a Bearer header if `jina_api_key` is set.
    - Raises `FetchError` if both paths yield fewer than 200 characters.
  - `PdfPage(number: int, text: str, tables: list[list[list[str | None]]])`, `PdfText(url, pages: list[PdfPage])`.
  - `async read_pdf(url: str, ctx: RunContext, pages: list[int] | None = None) -> PdfText`: 1-based page numbers, pdfplumber `extract_text()` and `extract_tables()`, with the parsing run in `asyncio.to_thread`.
  - `normalize(s: str) -> str`: NFKC; curly quotes become straight; en and em dashes become `-`; casefold; collapse whitespace.
  - `quote_in_text(quote: str, text: str, threshold: float = 0.9) -> bool`: exact substring after `normalize`. If that fails, and only when `len(normalize(quote)) >= 20`, use `rapidfuzz.fuzz.partial_ratio >= threshold * 100`. An empty quote returns `False`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_grounding_tolerates_typography():
    assert quote_in_text("inflation rose to “4.4%” — KNBS", "Inflation rose to \"4.4%\" - KNBS said")
def test_grounding_rejects_short_fuzzy():
    assert not quote_in_text("rate cut", "rates were held")
def test_grounding_rejects_fabricated_long_quote():
    assert not quote_in_text("the central bank cut the rate to 7 percent", "the central bank held the rate at 9 percent citing inflation")
def test_empty_quote_false(): assert not quote_in_text("", "anything")
async def test_fetch_page_extracts_article(respx_mock, ctx): ...      # an HTML fixture with an <article> → text contains its first paragraph, via="direct"
async def test_fetch_page_falls_back_to_jina(respx_mock, ctx): ...    # an HTML shell with an empty body → r.jina.ai is called, via="jina"
async def test_read_pdf_returns_table(respx_mock, ctx): ...           # pages[0].tables[0] has 2 rows and 3 columns
```

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: page fetch, PDF reader and quote grounding`.

---

### Task 11: Radar core, RSS and calendar adapters

**Files:**
- Create: `src/kenya_data_engine/radar/__init__.py`, `radar/base.py`, `radar/rss.py`, `radar/calendar.py`
- Test: `tests/test_radar_base.py`, `tests/test_radar_rss.py`, `tests/test_radar_calendar.py`

**Interfaces:**
- Consumes: `Signal`, `RadarResult`, `AdapterError`, `signal_id`, `normalize_url` (Task 5); `fetch` (Task 7); `RunContext` and `Stage` (Task 8); the Spike 3 fixtures.
- Produces:
  - `class Adapter(Protocol)` with `name: str` and `async fetch(ctx: RunContext, since: datetime) -> list[Signal]`.
  - `dedupe(signals: list[Signal]) -> list[Signal]`: keeps the first occurrence, keyed by `normalize_url(url)` when present and by the casefolded, whitespace-collapsed title otherwise. A signal is also dropped if its normalized title matches a signal already kept.
  - `async run_radar(adapters: list[Adapter], ctx, since: datetime) -> RadarResult`:
    - Runs the adapters concurrently, with a tracer span per adapter.
    - An exception from an adapter becomes `AdapterError(adapter.name, str(exc))`.
    - Results are deduped and sorted with `published_at` descending and `None` last.
  - `RssAdapter(name: str, url: str, kind: SignalKind = "news")`:
    - Parses with `feedparser` from fetched bytes.
    - Keeps entries with a parsed date ≥ `since` and drops entries without a date.
    - The snippet is the summary with HTML stripped, truncated to 300 characters.
    - The source is `name`.
  - `CalendarAdapter(path: Path, lookahead_days: int)` and `occurrences(entry: dict, start: date, end: date) -> list[date]`:
    - Signals have kind `calendar`, `url=None`, title `f"{name} — {d:%d %b %Y}"`, `published_at` set to midnight UTC on the occurrence date, and `meta={"category", "note"}`.
    - It emits occurrences in `[today, today + lookahead]` and ignores `since`.
  - `RadarStage` with `name="radar"`, `output_name="signals"` and `output_type=RadarResult`. `run(ctx, None)` sets `since = now - config.radar.since_hours`.
  - `build_adapters(config: EngineConfig, home: EngineHome) -> list[Adapter]` includes only adapters listed in `config.radar.enabled`. The Google Trends feed is `RssAdapter("google_trends", config.radar.trends_feed, kind="attention")`.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_failed_adapter_does_not_stop_others(ctx):        # Review Focus 2
    res = await run_radar([OkAdapter(n=3), BoomAdapter()], ctx, since=T0)
    assert len(res.signals) == 3 and res.errors[0].adapter == "boom"

def test_dedupe_by_url_and_title(): ...                         # same URL with utm param → 1; same title on different URLs → 1

async def test_rss_parses_fixture_and_filters_since(respx_mock, ctx): ...  # the captured Spike 3 fixture → ≥1 signal, all published_at ≥ since

async def test_rss_garbage_yields_zero_without_error(respx_mock, ctx):   # Review Focus 3
    respx_mock.get(URL).respond(200, content=b"\xff\xfe<html>not a feed")
    assert await RssAdapter("x", URL).fetch(ctx, T0) == []

async def test_rss_empty_feed(respx_mock, ctx): ...  # a valid feed with 0 items → []

def test_occurrences_monthly_last_day():
    assert occurrences({"rule": "monthly", "day": -1}, date(2026, 2, 1), date(2026, 3, 5)) == [date(2026, 2, 28)]
def test_occurrences_annual_and_date(): ...  # annual 12 Jun inside the window → one date; a date entry outside the window → []
async def test_timeout_adapter_reported(respx_mock, ctx): ...  # RssAdapter with a TransportError on every retry → errors has one entry, the run continues
```

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: radar core with RSS, Google Trends and calendar adapters`.

---

### Task 12: Listing adapters (CBK, KNBS, EPRA, Parliament)

**Files:**
- Create: `src/kenya_data_engine/radar/listing.py`
- Modify: `radar/base.py`: `build_adapters` adds a listing adapter for each entry in `config.radar.listings` that is enabled
- Test: `tests/test_radar_listing.py`

**Interfaces:**
- Consumes: `ListingSpec` (Task 4), the Spike 3 listing fixtures and selectors, `fetch` (Task 7).
- Produces: `ListingAdapter(name: str, spec: ListingSpec, max_items: int)`.
  - It parses with `selectolax`. Items match `spec.item`. Within each item, the title text comes from `spec.title`, the link `href` from `spec.link` (made absolute against `spec.url`), and the date from `spec.date` if set, parsed with `dateutil.parser.parse(fuzzy=True)`. Unparseable dates become `None`.
  - Dated items older than `since` are dropped. Undated items are kept.
  - It returns at most `max_items`, in page order. The signal kind is `spec.kind`.
  - If `spec.item` matches zero items on a 200 response, it raises `FetchError(f"{name}: selector matched nothing — page layout may have changed", hint="run engine doctor")`, so the change shows up as an adapter error.

- [ ] **Step 1: Write the failing tests**, parametrized over the four captured fixtures and their `defaults/config.yaml` specs.

```python
@pytest.mark.parametrize("name", ["cbk", "knbs", "epra", "parliament"])
async def test_listing_fixture_parses(name, respx_mock, ctx):
    sigs = await adapter_for(name).fetch(ctx, since=datetime(2000, 1, 1, tzinfo=UTC))
    assert 1 <= len(sigs) <= ctx.config.radar.max_items
    assert all(s.title.strip() and s.url.startswith("http") for s in sigs)

async def test_layout_change_raises_fetch_error(respx_mock, ctx): ...  # "<html><body>new layout</body></html>" → FetchError
async def test_relative_links_made_absolute(respx_mock, ctx): ...
```

Skip parametrized cases for adapters marked `deferred` in Spike 3, using `pytest.mark.skip` with the reason from RESULTS.md.

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: listing adapters for CBK, KNBS, EPRA and Parliament`.

---

### Task 13: LLM layer, prompts and clustering

**Files:**
- Create: `src/kenya_data_engine/llm.py`, `prompts/__init__.py`, `prompts/cluster.md`, `synth/__init__.py`, `synth/cluster.py`
- Test: `tests/test_llm.py`, `tests/test_cluster.py`

**Interfaces:**
- Consumes: Spike 1 findings (class names, `extra_body`); `RunContext`, `Tracer`; `Signal`.
- Produces:
  - `build_model(ctx: RunContext) -> pydantic_ai.models.Model`. It uses the OpenAI-compatible model and provider classes named in Spike 1, with `base_url=config.llm.base_url` and the DeepSeek key. If the key is missing, it raises `ConfigError("DEEPSEEK_API_KEY not set", hint="run engine init")`.
  - `stage_settings(ctx, stage: str) -> ModelSettings`, made from `config.llm.stages[stage]` (`max_tokens`, `extra_body`).
  - `async run_agent(agent: Agent[None, T], prompt: str, ctx, *, stage: str, name: str, topic_id: str | None = None, model: Model | None = None) -> T`:
    - Calls `ctx.tracer.check_budget()` first.
    - Runs the agent with `stage_settings`.
    - Records `record_llm` from `result.usage()`, even on failure, with whatever usage is available.
    - Returns `result.output`.
    - The `model` parameter is the test seam; tests pass a `FunctionModel`.
  - `load_prompt(name: str) -> str` reads `prompts/<name>.md` from package data.
  - `Cluster(title: str, summary: str, category: Category, signal_ids: list[str])`, `ClusterOutput(clusters: list[Cluster])`.
  - `format_signals(signals: list[Signal]) -> str`: one line per signal, in the form `id | kind | source | YYYY-MM-DD | title[:120]`.
  - `async cluster_signals(signals: list[Signal], ctx, *, model=None) -> tuple[list[Cluster], list[str]]`:
    - Truncates to `config.synth.max_signals`, keeping the most recent, with calendar signals always kept.
    - One agent call with `output_type=ClusterOutput` and stage `"synthesize_cluster"`.
    - Afterwards, drops unknown signal ids, then drops clusters with no ids left. Each dropped cluster's title is added to the returned `dropped` list as `"<title>: no valid signals"`.
- **`prompts/cluster.md` must contain:**
  - a 6–10 line Kenya personal-finance context primer
  - the task: group signals about the same underlying story or theme; one signal can be in at most one cluster; ignore signals that have no plausible money, economy, business, startup or law angle
  - the category list from the spec, verbatim
  - the rule "use only the ids given; never invent ids"
  - a 3-signal worked example with its expected JSON

- [ ] **Step 1: Write the failing tests.**

```python
async def test_unknown_ids_and_empty_clusters_dropped(ctx):     # Review Focus 4
    fm = function_model_returning(ClusterOutput(clusters=[
        Cluster(title="Fuel", summary="s", category="personal_finance", signal_ids=["a1", "zzz"]),
        Cluster(title="Ghost", summary="s", category="economy", signal_ids=["nope"])]))
    clusters, dropped = await cluster_signals([sig("a1"), sig("b2")], ctx, model=fm)
    assert [c.signal_ids for c in clusters] == [["a1"]] and dropped == ["Ghost: no valid signals"]

async def test_run_agent_records_usage_and_cost(ctx): ...      # a FunctionModel with known usage → one llm line in trace.jsonl, cost > 0
async def test_run_agent_checks_budget_first(ctx): ...         # tracer pre-loaded over budget → BudgetExceeded, model not called
def test_build_model_without_key_raises(ctx_no_keys): ...
def test_truncation_keeps_calendar_signals(ctx): ...           # max_signals=2 with 3 news and 1 calendar → the calendar signal is present
```

`function_model_returning(output)` lives in `conftest.py`. It builds a `FunctionModel` that answers with the structured-output tool call carrying `output`, using the Pydantic AI testing pattern the Spike 1 notes point to.

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: LLM layer with tracing, prompt loader and signal clustering`.

---

### Task 14: Scoring, ranking and SynthesizeStage

**Files:**
- Create: `src/kenya_data_engine/prompts/score.md`, `synth/score.py`, `synth/stage.py`
- Test: `tests/test_score.py`, `tests/test_synth_stage.py`

**Interfaces:**
- Consumes: `cluster_signals`, `run_agent`, `load_prompt` (Task 13); `Topic`, `TopicScores`, `TopicList`, `RadarResult` (Task 5).
- Produces:
  - `ScoreOutput(data_ability, wallet_impact, timeliness, clarity_gap, novelty: int  # 1..5, justification: dict[str, str], why_now: str)`.
  - `weighted_score(scores: TopicScores, weights: dict[str, float]) -> float` returns `round(sum(weights[k] * getattr(scores, k)), 3)`.
  - `rank_topics(topics: list[Topic], top_n: int) -> list[Topic]` sorts by `final_score` descending, then by `len(signal_ids)` descending, then by title ascending, and keeps the first `top_n`.
  - `async score_cluster(cluster: Cluster, signals: dict[str, Signal], ctx, *, model=None) -> Topic`:
    - The prompt contains the cluster and its signals' formatted lines.
    - Stage `"synthesize_score"`.
    - The topic id is `signal_id(None, cluster.title)`.
    - `final_score` is computed in code.
  - `SynthesizeStage` with `name="synthesize"`, `output_name="topics"`, `output_type=TopicList`. `run(ctx, radar: RadarResult)`:
    - Clusters the signals.
    - Scores clusters concurrently, limited by an `asyncio.Semaphore(config.concurrency)`.
    - If any score raises `BudgetExceeded`, it stops launching new scores, keeps the finished ones, and sets `budget_exhausted=True`.
    - Other per-cluster exceptions are added to `dropped` as `"<title>: scoring failed: <msg>"`.
    - Returns `TopicList(topics=rank_topics(...), dropped=..., budget_exhausted=...)`.
    - Takes an optional `model` attribute for tests.
- **`prompts/score.md` must contain:**
  - the same Kenya primer as `cluster.md`, kept in sync by including it from `prompts/_primer.md`
  - the five rubric criteria with their questions, verbatim from spec §4.2
  - anchors for score 1 and score 5 on each criterion
  - the rule "score only; do not compute totals; do not state statistics you were not given"
  - a `why_now` of at most 25 words

- [ ] **Step 1: Write the failing tests.**

```python
def test_weighted_score_uses_spec_weights():
    s = TopicScores(data_ability=5, wallet_impact=4, timeliness=3, clarity_gap=2, novelty=1, justification={})
    assert weighted_score(s, DEFAULT_WEIGHTS) == pytest.approx(0.35*5 + 0.20*4 + 0.20*3 + 0.15*2 + 0.10*1)

def test_rank_ties_break_on_signal_count_then_title(): ...
async def test_stage_returns_top_n_ranked(ctx): ...           # 7 clusters with scripted scores, top_n 5 → 5 topics in descending order
async def test_budget_exhaustion_keeps_partial(ctx): ...      # budget hits after 2 scores → 2 topics, budget_exhausted True
async def test_scoring_failure_recorded_in_dropped(ctx): ...  # one cluster's model raises → its title is in dropped, the others are returned
async def test_llm_score_out_of_range_is_retried_or_dropped(ctx): ...  # the model returns 7 → Pydantic AI retry; if it still fails → dropped
```

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: rubric scoring, code-weighted ranking and synthesize stage`.

---

### Task 15: Rich CLI shell, `engine run` and `engine stage`

**Files:**
- Create: `src/kenya_data_engine/cli/ui.py`, `cli/run.py`, `cli/stage.py`
- Modify: `cli/app.py` (global options, error handler, sub-commands)
- Test: `tests/test_cli_run.py`

**Interfaces:**
- Consumes: `run_pipeline`, `open_context` (Task 8); `RadarStage` (Task 11); `SynthesizeStage` (Task 14); `RunStore` (Task 5).
- Produces:
  - **Global options:** `--home PATH`, `--verbose/-v`, `--quiet/-q`, `--version`.
  - **Error handling:** `EngineError` prints a red `✗ <message>` and a dim `→ <hint>`, then exits with code 2. Any other exception prints `✗ unexpected error: <msg>` with "re-run with -v for details", then exits with code 1. A traceback appears only with `-v`.
  - **`cli/ui.py`:**
    - `console`, with a theme containing `ok`, `warn`, `fail`, `muted`, `accent` and `score` styles.
    - `badge(status) -> Text`.
    - `topics_table(tl: TopicList) -> Table`, with columns # / Topic / Category / Score / Why now / Signals.
    - `stage_progress()`, which returns an `emit` callback driving a Rich `Progress` with one row per stage (spinner, then ✓ / ↷ skipped / ✗).
    - `run_footer(run: RunHandle, tracer: Tracer) -> Panel`, showing the run id, the cost against the budget, the elapsed time, the error count and the artifact path.
  - **`engine run [--top N] [--since HOURS] [--resume RUN_ID]`:**
    - Builds `[RadarStage(), SynthesizeStage()]`. `--top` and `--since` override the config for this run.
    - Then prints the progress, the topics table, the dropped and adapter-error notes (yellow), and the footer.
    - If `budget_exhausted`, a yellow notice is shown.
  - **`engine stage radar|synthesize [--run RUN_ID] [--input PATH]`:**
    - Runs one stage.
    - The input comes from `--input` (JSON file) or from the previous artifact of `--run`. A new run is created if neither is given, which only works for `radar`. Otherwise it raises `EngineError("synthesize needs --input or --run", hint=...)`.
    - Prints the artifact path.
  - **`--json` on `run`:** prints the `TopicList` JSON to stdout instead of the table. The progress bar goes to stderr.

- [ ] **Step 1: Write the failing tests** (CliRunner with stages monkeypatched to fakes, and a tmp home).

```python
def test_run_prints_ranked_table(cli, fake_stages): ...       # exit 0; stdout has topic titles in rank order and "$"
def test_missing_key_is_friendly(cli_no_keys):                # Review Focus 1
    r = cli_no_keys.invoke(app, ["run"])
    assert r.exit_code == 2 and "engine init" in r.stdout and "Traceback" not in r.stdout
def test_verbose_shows_traceback_on_unexpected(cli, boom_stage): ...
def test_stage_synthesize_requires_input(cli): ...            # exit 2 with the hint
def test_run_json_output_is_valid(cli, fake_stages): ...      # TopicList.model_validate_json(stdout)
def test_resume_flag_skips_radar(cli, fake_stages): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Check by hand.** Run `uv run engine run --help` and `uv run engine stage --help`. Expected: each command shows a description and at least one example in its docstring epilog.
- [ ] **Step 6: Commit** with `feat: rich CLI with run and stage commands`.

---

### Task 16: `engine init` and `engine doctor`

**Files:**
- Create: `src/kenya_data_engine/cli/init.py`, `cli/doctor.py`
- Modify: `cli/app.py` (register the commands)
- Test: `tests/test_cli_init.py`, `tests/test_doctor.py`

**Interfaces:**
- Consumes: `EngineHome`, `load_config`, `load_secrets` (Task 4); `build_search` (Task 9); `build_adapters` (Tasks 11–12); `open_context` (Task 8).
- Produces:
  - `Check(name: str, group: Literal["keys", "llm", "search", "sources"], status: Literal["ok", "warn", "fail"], latency_ms: int | None, detail: str)`.
  - `async run_checks(ctx) -> list[Check]`:
    - **Keys:** DeepSeek missing means fail; no search key at all means fail; only one search key means warn.
    - **LLM:** `GET {base_url}/models` with the key. A 200 that lists `deepseek-flash` means ok; a 200 without it means warn, with "model not listed"; anything else means fail.
    - **Search:** one query, "Kenya inflation", with n=1, through `build_search`.
    - **Sources:** each enabled adapter's `fetch(ctx, since=now-30d)`. One or more signals means ok; 0 means warn; an exception means fail with its message.
    - All checks run concurrently. Checks never raise.
  - **`engine doctor [--json]`:** a Rich table grouped by group, with status badge, name, latency and detail, then the line "N ok · N warn · N fail". It exits 1 if any check fails, otherwise 0. With `--json`, it prints a list of `Check`s.
  - **`engine init [--force] [--no-verify] [--non-interactive]`:**
    - Calls `home.ensure()`.
    - Copies the packaged `config.yaml` and `calendar.yaml` into the home if they're absent. They are overwritten only with `--force`, and a skipped file shows "kept existing".
    - Prompts with Rich prompts, using hidden input, for the DeepSeek key (required), Tavily (recommended), and Serper and Jina (optional, Enter to skip).
    - With `--non-interactive`, it takes keys from the environment instead.
    - Unless `--no-verify`, it checks the DeepSeek key via `/models` and re-prompts up to 3 times on failure.
    - Writes `.env` with mode `0o600`, preserving unrelated existing lines.
    - Finishes by running doctor and printing "Next: engine run".

- [ ] **Step 1: Write the failing tests.**

```python
def test_init_non_interactive_writes_env_0600(cli, monkeypatch, tmp_home): ...  # .env contains DEEPSEEK_API_KEY; oct(mode & 0o777) == "0o600"
def test_init_does_not_overwrite_config_without_force(cli, tmp_home): ...
def test_init_preserves_unrelated_env_lines(cli, tmp_home): ...
async def test_doctor_missing_deepseek_key_fails(ctx_no_keys): ...  # the keys check has status "fail"
async def test_doctor_source_exception_becomes_fail_check(respx_mock, ctx): ...  # it never raises
def test_doctor_exit_code_1_on_fail(cli_no_keys): ...
def test_doctor_json(cli, monkeypatch): ...  # validates as list[Check]
```

- [ ] **Step 2: Run them.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat: engine init wizard and doctor health checks`.

---

### Task 17: End-to-end check and README

**Files:**
- Create: `tests/test_e2e.py`
- Modify: `README.md` (usage, commands, config reference, troubleshooting)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the end-to-end test.**
  - Set up a tmp home via `engine init --non-interactive --no-verify` with fake keys.
  - Use respx to serve the Spike 3 fixtures for all enabled adapters.
  - Patch `SynthesizeStage.model` with a `FunctionModel` that returns scripted clusters and scores.
  - Invoke `engine run --top 3`.
  - Assert:
    - exit code 0
    - `runs/<id>/signals.json`, `topics.json`, `trace.jsonl` and `summary.json` exist
    - `topics.json` has at most 3 topics, sorted by `final_score`
    - every topic's `signal_ids` is a subset of the signal ids
    - no API-key value appears in any file under `runs/`

- [ ] **Step 2: Run it.** `uv run pytest tests/test_e2e.py -v`. Expected: PASS. Fix any integration gaps it shows in the owning module, with a test added there.

- [ ] **Step 3: Run the full gate.** `make check`. Expected: all green, coverage ≥85%.

- [ ] **Step 4: Live smoke test (needs real keys; not part of CI).** `engine init`, then `engine doctor`, then `engine run`. Expected:
  - doctor shows no `fail` in keys or LLM
  - run prints a ranked table
  - the footer cost is under $0.50

  Record the actual cost and time in `spikes/RESULTS.md` under "First live run".

- [ ] **Step 5: Finish the README.** Cover:
  - each command with an example
  - the config keys table (from `EngineConfig`)
  - how to add an RSS feed or a calendar entry
  - troubleshooting, mapping each doctor failure to its fix

- [ ] **Step 6: Commit** with `test: end-to-end pipeline test; docs: README usage`.
