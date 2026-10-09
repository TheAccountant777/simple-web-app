# Kenya Data Engine — Plan 3a: Engine Room (TUI, live view, performance report)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `engine tui` is a full-screen terminal app that shows the engine's inner workings. It has 4 tabs:
- **Live:** watch a run happen.
- **Runs:** explore results, the maths behind each score, and the exact LLM inputs and outputs.
- **Sources:** source health, with live tests.
- **Performance:** speed, cost and reliability across runs.

`engine report` gives the performance numbers on the plain command line.

**Architecture:**
- **Live feed:** the Tracer gains in-process subscribers.
- **Data:** every number on screen comes from the trace events the engine already writes, enriched with HTTP and adapter attributes, plus the run artifacts and new per-call LLM capture files.
- **Separation:** a pure `report.py` turns run folders into metrics, and the TUI and `engine report` both read it. The TUI is Textual. It runs the pipeline in a worker for live mode and only reads files otherwise.

**Tech Stack:** Textual, Rich, plus the existing stack. Tests use Textual's `App.run_test()` pilot.

**Spec:** `docs/superpowers/specs/2026-10-09-kenya-data-engine-design.md` §6.1 (live run view, `engine browse` TUI, which is renamed here to `engine tui`, keeping `browse` as an alias) and §8 (tracing). The design addition was approved in chat on 2026-10-09: the "expensive watch" engine room, a scored-maths view, LLM exchange capture, and a performance tab.

## Global Constraints
- Python ≥3.12. Textual is pinned `>=1,<2` (or the current major at implementation time, pinned with an upper bound).
- Every number shown in the TUI or report is derived from `trace.jsonl`, `summary.json`, run artifacts, `llm/*.json` or source health. No number is computed by an LLM.
- **Secrets:** LLM capture files and every TUI string pass through `Tracer.redact` (or the same function) before they are written or displayed. Never display `.env` contents.
- Tests never touch the network. TUI tests use `run_test()` with a tmp home holding synthetic runs.
- **Keyboard first:** every action has a key, and the footer always shows the keys. `q` quits and `?` opens help. The layout must work at 100×30, and nothing important may be clipped at 80×24.
- **No new state store.** The TUI reads what the engine writes, and live mode uses the Tracer subscription.
- `make check` stays green, with coverage ≥85%. UI glue (CSS and layout composition) can be excluded with `# pragma: no cover` only where pilot tests cannot reach it, and each exclusion must be justified in a comment.

## Review Focus
1. **Empty or first-run home** (no runs, no health): every tab shows a friendly empty state with the next action (for example "No runs yet — press r on Live to start one"). Owner: Tasks 3 and 6.
2. **A run that crashed mid-way** (missing `topics.json`, partial trace): Runs and Performance show what exists, mark the run "incomplete", and never raise. Owner: Tasks 2 and 3.
3. **Live run fails** (missing key, budget exhausted, provider error): Live shows the error and its hint in a panel, the stage turns red, and the app stays usable. Owner: Task 4.
4. **Very long content** (large prompts, 300 signals, long titles): views scroll, and nothing freezes or overflows. Owner: Tasks 3 and 4.
5. **Terminal resize during a live run:** the layout reflows and the event stream continues. Owner: Task 4.

---

### Task 1: Trace enrichment, live subscription and LLM capture
**Files:** Modify `trace.py`, `http.py`, `radar/base.py`, `llm.py`, `config.py`, `defaults/config.yaml`. Test `tests/test_trace_live.py`, `tests/test_llm_capture.py`, and extend `tests/test_http.py`.

**Interfaces (produces):**
- `Tracer.subscribe(fn: Callable[[TraceEvent], None]) -> Callable[[], None]`. It returns an unsubscribe function. Subscribers are called after redaction, in the order they subscribed. A subscriber that raises is unsubscribed and logged once; it never breaks recording.
- `Tracer.span(...)` now yields a mutable `dict[str, Any]` of attrs, which are merged into the recorded event: `async with tracer.span(...) as attrs: attrs["signals"] = n`. Existing callers that ignore the yield keep working.
- `fetch(..., tracer: Tracer | None = None)` records one `kind="http"` event per call, named by the URL's host. Attrs are `url` (redacted), `status`, `from_cache: bool`, `bytes` and `attempts`; `latency_ms` is set. Every call site passes `ctx.tracer`.
- Radar adapter spans set `attrs["signals"] = len(result)`.
- **LLM capture:**
  - Config `trace.capture_llm: bool = True` (new `TraceConfig` model).
  - When it is on, `run_agent` writes `<run>/llm/<seq:04d>-<stage>-<name>.json`, where `seq` is per-run and monotonic, holding:
    - identification: `seq`, `stage`, `name`, `topic_id`, `model`, `settings` (`max_tokens`, `extra_body`)
    - exchange: `messages` (Pydantic AI `result.all_messages()` dumped with its JSON adapter) and `output` (model_dump)
    - outcome: `usage` (in/out tokens), `latency_ms`, `cost_usd`, `status`, `error`
  - On failure, `messages` holds what is available (at least the user prompt) and `error` holds the redacted message.
  - The whole JSON is redacted before it is written. The trace `llm` event gains `attrs["capture"] = "<filename>"`.

- [ ] **Step 1: Write the failing tests.**
  - `test_subscriber_receives_redacted_events`
  - `test_raising_subscriber_is_dropped_not_fatal`
  - `test_span_attrs_merged`
  - `test_fetch_records_http_event_with_cache_flag`: the second call has `from_cache` True
  - `test_radar_span_records_signal_count`
  - `test_llm_capture_file_written_and_redacted`: plant a secret value in the prompt and assert it is absent from the file
  - `test_llm_capture_on_failure`
  - `test_capture_disabled_writes_nothing`
- [ ] **Step 2: Run them and confirm RED.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm GREEN, then run `make check`.**
- [ ] **Step 5: Commit** with `feat: live trace subscription, http/adapter attrs, LLM capture`.

### Task 2: Metrics model and `engine report`
**Files:** Create `src/kenya_data_engine/report.py`, `cli/report.py`. Modify `cli/app.py`. Test `tests/test_report.py`, `tests/test_cli_report.py`, with a helper `tests/runs_factory.py` that writes synthetic run folders.

**Interfaces (produces):**
- `SourceStat(name, status: Literal["ok","error"], latency_ms, signals: int | None, error: str | None)`
- `StageStat(name, status, duration_ms, detail: str)`
- `LlmStat(calls, input_tokens, output_tokens, cost_usd, mean_latency_ms, errors)`
- `RunMetrics(run_id, started_at, duration_ms, complete: bool, cost_usd, budget_usd, stages: list[StageStat], sources: list[SourceStat], llm: LlmStat, http_requests, cache_hits, cache_hit_rate: float | None, topics: int, dropped: int, errors: int)`
- `load_run_metrics(run: RunHandle, budget_usd: float) -> RunMetrics`. It reads `trace.jsonl` and `topics.json` when present, tolerates bad lines and missing files, and sets `complete=False` when the last stage has no done event.
- `Aggregate(runs: int, stage_p50_ms: dict[str,int], stage_p95_ms: dict[str,int], source_success_rate: dict[str,float], source_mean_latency_ms: dict[str,int], cost_per_run: list[tuple[str,float]], duration_per_run: list[tuple[str,int]], mean_cache_hit_rate: float | None)`
- `build_report(runs: list[RunMetrics]) -> Aggregate`. Percentiles use the nearest-rank method, and the source success rate is ok ÷ attempts.
- `engine report [RUN_ID] [--last N=10] [--json]`:
  - With a RUN_ID: one run's panel, a stage table, a source table sorted by latency descending, and an LLM table.
  - Without one: the aggregate across the last N runs, as tables plus a Rich sparkline-style cost and duration trend.
  - With `--json`: `{"runs": [...RunMetrics], "aggregate": Aggregate}`.
  - Empty home: "No runs yet — run `engine run`." Exit 0.

- [ ] **Step 1: Write the failing tests.**
  - `test_metrics_from_complete_run`
  - `test_incomplete_run_flagged` (Review Focus 2)
  - `test_cache_hit_rate`
  - `test_percentiles_nearest_rank`
  - `test_source_success_rate_across_runs`
  - `test_report_json_shape`
  - `test_report_empty_home`
- [ ] **Step 2: Run them and confirm RED. Step 3: Implement. Step 4: Run them and confirm GREEN, then run `make check`.**
- [ ] **Step 5: Commit** with `feat: run metrics and engine report`.

### Task 3: TUI shell and Runs tab (results, score maths, LLM inspector)
**Files:** Create `src/kenya_data_engine/tui/__init__.py`, `tui/app.py` (`EngineRoom(App)`), `tui/runs.py`, `tui/widgets.py`, `tui/theme.tcss`, `cli/tui.py`. Modify `cli/app.py` (commands `tui` and alias `browse`). Test `tests/test_tui_runs.py`.

**Interfaces (produces):**
- `EngineRoom(home: EngineHome, *, live_stages_factory: Callable[[], list[Stage]] | None = None)`. Tabs are Live / Runs / Sources / Performance, switched with keys `1`–`4`. The header shows the engine home and run count. `q` quits and `?` opens the help modal.
- **Runs tab, three panes:**
  - **Run list:** newest first, each row with id, ✓ or ⚠ incomplete, topics and cost.
  - **Topic table for the selected run:** rank, title, category, score.
  - **Topic detail:**
    - Score maths: one row per criterion showing its score bar (1–5) × weight = contribution, then the sum = `final_score`, then the justification, all from `topics.json` and config weights. The displayed sum must equal the stored `final_score` to 3 decimals; if it doesn't, show ⚠ "weights changed since run".
    - Why now.
    - Signals: titles, sources and dates from `signals.json`, with `o` to open the URL.
    - The LLM exchanges for that topic, from `llm/*.json` matched by `topic_id`, plus the cluster call for the run.
- **LLM inspector modal** (press `enter` on an exchange):
  - shows the model and settings, tokens, cost and latency;
  - shows the messages as role-labelled blocks, with system/instructions, user prompt and the model's tool call or text;
  - shows the parsed output as pretty JSON;
  - is scrollable, and `c` copies the output JSON to the clipboard where supported.
- **Empty state:** "No runs yet — press 1 then r to start one".

- [ ] **Step 1: Write the failing pilot tests.**
  - `test_runs_tab_lists_runs_newest_first`
  - `test_topic_detail_shows_score_maths_matching_final`
  - `test_weights_mismatch_warning`
  - `test_llm_inspector_shows_prompt_and_output`
  - `test_incomplete_run_renders` (Review Focus 2)
  - `test_empty_home_message` (Review Focus 1)
  - `test_long_prompt_scrolls_without_error` (Review Focus 4)
- [ ] **Step 2: Run them and confirm RED. Step 3: Implement. Step 4: Run them and confirm GREEN, then run `make check`.**
- [ ] **Step 5: Commit** with `feat: engine tui shell with runs explorer, score maths and LLM inspector`.

### Task 4: Live tab (the movement)
**Files:** Create `tui/live.py`. Modify `tui/app.py`. Test `tests/test_tui_live.py`.

**Interfaces:**
- Consumes: `Tracer.subscribe`, `run_pipeline`, `open_context`, `RunStore` and the default stages `[RadarStage(), SynthesizeStage()]`, injected through `live_stages_factory` for tests.
- Key `r` starts a run in a Textual worker. Key `x` cancels a running run, which cancels the worker; the pipeline's summary is still written. Only one run can be active at a time.
- **Widgets:**
  - **Pipeline strip:** one box per stage, connected by arrows. States are idle, running (animated spinner plus elapsed time), done (✓ plus duration plus `describe()` detail), error (✗) and skipped (↷).
  - **Source grid:** a tile per adapter showing pending, then ✓ or ✗, latency and signals, all from radar span events.
  - **Event stream:** a `RichLog` holding one formatted line per TraceEvent, with time, kind icon, name, latency, tokens/cost or cache hit/miss, redacted. It is capped at 2,000 lines and autoscrolls unless the user scrolls up.
  - **Gauges:** cost against budget (ProgressBar plus $), tokens in and out, elapsed time, HTTP requests and cache hit rate, LLM calls.
- **Completion:** shows a summary panel, then offers `enter` to open the new run in the Runs tab.
- **Failure:** an error panel with the `EngineError` message and hint (Review Focus 3). The app stays responsive.
- Tracer events arrive from the worker. UI updates go through `call_from_thread` or `post_message`, never touching widgets directly from another thread or task.

- [ ] **Step 1: Write the failing pilot tests**, using fake stages that emit known trace events.
  - `test_live_run_updates_pipeline_and_gauges`
  - `test_source_tiles_from_radar_spans`
  - `test_event_stream_lines_redacted`
  - `test_live_failure_shows_hint_and_app_survives`
  - `test_cancel_run`
  - `test_resize_during_run` (`pilot.resize_terminal`; Review Focus 5)
- [ ] **Step 2: Run them and confirm RED. Step 3: Implement. Step 4: Run them and confirm GREEN, then run `make check`.**
- [ ] **Step 5: Commit** with `feat: live engine view in tui`.

### Task 5: Sources tab
**Files:** Create `tui/sources.py`. Test `tests/test_tui_sources.py`.

**Interfaces:**
- Consumes: the sources loader and health API delivered by the source-upgrade work. These are the functions behind `engine sources list` and `engine sources test`; reuse them rather than duplicating logic.
- **Table:** name, type, kind, enabled, health (ok / failing ×N / never), last ok, last error. Filter with `/`.
- **Testing:** `t` tests the selected source live, without the cache, in a worker, and shows status, latency and the first 5 items, or the error and its hint. `T` tests all sources with a progress bar.
- **Editing:** `e` shows the path of the user `sources.yaml` and the merged entry for the selected source as YAML, ready to copy. The TUI does not edit files.

- [ ] **Step 1: Write the failing tests.**
  - `test_sources_table_shows_health`
  - `test_test_selected_source_shows_items` (mocked)
  - `test_failing_source_shows_hint`
  - `test_filter`
- [ ] **Step 2: Run them and confirm RED. Step 3: Implement. Step 4: Run them and confirm GREEN, then run `make check`.**
- [ ] **Step 5: Commit** with `feat: sources tab with live tests`.

### Task 6: Performance tab
**Files:** Create `tui/performance.py`. Test `tests/test_tui_performance.py`.

**Interfaces:**
- Consumes: `load_run_metrics` and `build_report` (Task 2).
- **Top:** KPI tiles for runs, mean duration, mean cost per run, mean cache hit rate and LLM error rate.
- **Middle:** Sparklines for cost per run and duration per run, each with min and max labels.
- **Bottom:** a stage latency table (p50 and p95) and a source reliability table (success rate, mean latency, last error), sorted worst first.
- `--last N` is selectable with `[` and `]`.
- **Empty state:** "Run the engine a few times to see trends" (Review Focus 1).

- [ ] **Step 1: Write the failing tests.**
  - `test_kpis_match_report`
  - `test_source_table_sorted_worst_first`
  - `test_empty_state`
- [ ] **Step 2: Run them and confirm RED. Step 3: Implement. Step 4: Run them and confirm GREEN, then run `make check`.**
- [ ] **Step 5: Commit** with `feat: performance tab`.

### Task 7: Polish, docs and screenshots
**Files:** Modify `README.md`. Create `docs/screenshots/*.svg` and `tests/test_tui_smoke.py`.

- [ ] **Step 1:** Write a smoke pilot test. It opens the TUI on a factory home with 3 runs (one incomplete), visits all 4 tabs, opens one LLM inspector and quits, with no exceptions, at 100×30 and at 80×24.
- [ ] **Step 2:** Generate SVG screenshots of each tab from the same factory home with `app.save_screenshot()`, via a `make screenshots` target that is not part of `check`.
- [ ] **Step 3:** Add a README "Engine Room" section covering each tab, the key map table and the screenshots. Document `engine report`.
- [ ] **Step 4: Run `make check`.** Expected: green.
- [ ] **Step 5: Commit** with `docs: engine room guide and screenshots`.
