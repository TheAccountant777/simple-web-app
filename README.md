# Kenya Data Engine

A local research engine for Kenyan data journalism. It collects fresh signals (news, data
releases, policy, search attention, a calendar of recurring events), clusters them into candidate
topics, scores each topic with DeepSeek, and prints a ranked list. The LLM only clusters and gives
1 to 5 rubric scores; the final score and the ranking are computed in code.

## Install

```
uv tool install git+https://github.com/TheAccountant777/simple-web-app
```

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

## Quickstart

```
engine init      # create ~/.kenya-data-engine, save your API keys (hidden input)
engine doctor    # check keys, the LLM, web search and every source
engine run       # collect, cluster, score, rank
```

You need a DeepSeek key (required) and a Tavily or Serper key (at least one; both gives fallback).
Jina is optional. Keys are stored only in `~/.kenya-data-engine/.env` (mode 600) or the
environment, and are never written to runs, traces or logs.

## Commands

Global options go before the command: `--home PATH`, `-v/--verbose`, `-q/--quiet`, `--version`.

### `engine run`

Runs the full pipeline (Radar, then Synthesize) and prints the ranked topics, any dropped clusters
or failed sources, and a run summary (cost against budget, time, errors, artifact path).

```
engine run                          # default top_n and look-back window
engine run --top 3 --since 24       # 3 topics, signals from the last 24 hours
engine run --resume 2026-10-09-0800 # reuse finished stages of an earlier run
engine run --json > topics.json     # JSON on stdout; progress goes to stderr
```

### `engine stage radar|synthesize`

Runs one stage on its own and prints the artifact path.

```
engine stage radar                                 # new run, writes signals.json
engine stage synthesize --run 2026-10-09-0800      # score the signals of an existing run
engine stage synthesize --input ./signals.json     # score a signals file
```

### `engine doctor`

Live health checks, grouped: API keys, the LLM (`GET /models`), web search (one query), config
warnings, and each enabled Radar source (with consecutive failures). Exits 1 if any check fails.

```
engine doctor
engine doctor --json
```

### `engine init`

Creates the home, writes a short override-only `config.yaml`, a commented `sources.yaml` template
and a full `calendar.yaml` (each only if absent), asks for your keys, verifies the DeepSeek key,
then runs doctor.

```
engine init                  # interactive; Enter keeps a saved key
engine init --force         # also overwrite config.yaml and calendar.yaml
engine init --reset-config  # back up config.yaml to config.yaml.bak-<timestamp>, write a fresh short one
engine init --no-verify     # skip the key check and doctor (offline)
DEEPSEEK_API_KEY=... TAVILY_API_KEY=... engine init --non-interactive
```

### `engine sources`

For auditing the Radar source list (see [Sources](#sources)).

```
engine sources list [--json]                 # name, type, kind, enabled, health
engine sources test <name> [--json]          # live fetch (cache bypassed) of one source
engine sources test --all [--json]           # every enabled source
```

`test` shows status, latency, item count and the first 5 extracted items (`title | date | link`),
or the exact error with a hint. It exits 0 only if every tested source returned items, and
`--json` output has a stable shape for scripts.

### `engine data`, `engine catalog probe`, `engine gc`

The data warehouse: dated, quote-free numbers with provenance, from `defaults/catalog.yaml`
(merged with `~/.kenya-data-engine/catalog.yaml` by key). Only the two World Bank series ship
enabled; every other entry is unverified until you probe it on your laptop.

```
engine data list [--json]                    # keys, adapter, tier, enabled, rows stored
engine data fetch <key> [--limit N] [--json] # discover, download, extract, check, store
engine data show <key> [--entity E] [--last N] [--csv]
engine catalog probe [KEY...] [--all] [--save-samples DIR] [--json]
engine gc [--older-than 90d] [--yes]         # prune blobs no stored row references
```

`fetch` exits 1 on an error or a quarantined table (the blob is kept, nothing is stored).
`probe` is diagnostic (no rows written, exit 0): it runs discovery live, reports the final URL,
links found and the sniffed type of the first item, and with `--save-samples` keeps up to two
items per key plus `probe.json` for writing a parser.

### `engine research`, `engine dossiers`, `engine memory`, `engine eval`

Takes one topic to a checked dossier. The topic is a rank from the latest `engine run`, a
`<run-id>:<topic-id>`, or free text. The planner reads it and decides whether it can be answered
with Kenyan data (`supported`, `reframed` or `reject`); scouts then find sources in rounds, code
computes every figure, a claim writer drafts claims that must quote their source exactly, and the
claims are verified before anything is called a fact. Progress prints one line per step, need and
claim, with the budget gauge after each step.

```
engine research 1                                # top topic of the latest run, standard budget
engine research "Kenya fuel prices and VAT" --budget lean
engine research 2 --plan-only                    # the plan and the verdict; no dossier
engine research 1 --force                        # research even if the planner rejects it
engine research --resume 2026-10-09-1530         # continue after a crash (TOPIC is remembered)
engine research 1 --json > outcome.json          # JSON on stdout; progress goes to stderr
engine dossiers list [--json]                    # date, NN, slug, verdict, facts, cost
engine dossiers show latest                      # or NN-slug, or a folder path
engine memory list [--json]                      # remembered sources and covered topics
engine memory forget "<signature>"               # as printed by `memory list`
engine eval [--only NAME] [--budget lean]        # acceptance scenarios, compared with the last run
```

Needs the DeepSeek key and one search key. Exit code 0 means a dossier was written (a rejected or
partial one counts); 1 means an error. Budgets are `lean`, `standard` and `deep` (dollars, search
credits and seconds, in `research.presets`); when the money or time runs low the dossier is still
written from what was found, and its scorecard says why the run stopped.

A dossier lives in `~/.kenya-data-engine/briefs/<date>/NN-<slug>/`: `README.md` (verdict, the top
facts with a source link and page, chart concepts marked ready, partial or not possible, gaps,
scorecard and a 15-minute check list), `brief.md`, `data/*.csv`, `stats.md`, `sources.json`,
`gaps.md`, `research/claims.json`, `research/comparisons.csv`, `research/verification.md` and
`dossier.json`. A rejected topic gets only the README, `brief.md` and `dossier.json`.

`engine eval` runs the scenarios in `defaults/eval.yaml` (`fuel`, `cbk_rate`, `weak`; add or
override with `~/.kenya-data-engine/eval.yaml`), spends real money and credits, stores each result
in the `eval_runs` table and exits 1 if any scenario misses its expectations. `engine report`
also prints a Research section: cost per dossier, facts per dollar and gap rate.

## Engine Room (TUI) and comparing runs

```bash
engine tui            # alias: engine browse
```

| Key | Action |
|---|---|
| `1` / `2` / `3` | Live / Runs / Sources tab |
| `r` / `R` | Live: new run / run ×N (2–5 back to back) · `x` cancels |
| `space`, then `c` | Runs: select runs, then compare them (with no selection, `c` compares the last 3) |
| `enter`, `i`, `o` | Open a topic · inspect the LLM exchange · open the source URL |
| `t` / `T` / `/` | Sources: test the selected source / test all / filter |
| `?`, `q` | Help · quit |

- **Live** shows the engine running: pipeline stages, source tiles (✓/✗, latency, signals), a streaming
  event log (HTTP, cache hits, LLM tokens and cost), and a cost-vs-budget gauge.
- **Runs** shows the ranked topics. For each one you see how the score was built (each criterion ×
  its weight, adding up to the final score), the signals behind it, and the exact prompts and outputs
  of every LLM call.
- **Compare** groups the same story across runs by the signals they share. For each topic it shows
  how many runs it appeared in (k/N), its mean score and range, its mean rank, and a stability badge
  (strong, mixed or noise). All of this is computed in code. Run 3–4 times and trust the topics that
  are strong.

```bash
engine compare --last 3        # consensus across your last 3 runs (--json for scripts)
engine report                  # speed, cost, cache and source reliability across runs
```

## Where things live

Default home: `~/.kenya-data-engine/` (override with `--home` or `ENGINE_HOME`).

```
config.yaml   sources.yaml   calendar.yaml   .env   engine.db   certs/   runs/<YYYY-MM-DD-HHMM>/   briefs/
```

`certs/` caches intermediate certificates the engine downloaded for servers with an incomplete TLS
chain (see [Troubleshooting](#troubleshooting)). `engine.db` also holds the HTTP cache and the
per-source health table.

A research run directory holds `research/` (one checkpoint per step, `evidence.json` and the
fetched texts) instead. Each `engine run` directory holds `signals.json` (Radar), `topics.json` (Synthesize), `trace.jsonl` (every
span and LLM call with tokens and cost) and `summary.json`. A truncated or corrupt artifact counts
as missing, so `--resume` simply re-runs that stage.

## Configuration

`~/.kenya-data-engine/config.yaml` is deep-merged over the packaged defaults, so you only write
the keys you want to change. `engine init` creates it as a short, commented file: **put overrides
there, not a copy of the defaults**, so new defaults reach you when you update. Radar sources are
configured separately in `sources.yaml`.

| Key | Default | Meaning |
|---|---|---|
| `top_n` | `5` | Topics kept in the ranking |
| `concurrency` | `4` | Parallel scoring calls |
| `cache_ttl_hours` | `6.0` | How long fetched pages and feeds are cached |
| `weights` | `data_ability 0.35, wallet_impact 0.20, timeliness 0.20, clarity_gap 0.15, novelty 0.10` | Score weights; exactly these keys, must sum to 1.0 |
| `budgets.run_usd` | `0.50` | Hard budget per run; scoring stops when reached |
| `llm.base_url` | `https://api.deepseek.com` | OpenAI-compatible endpoint |
| `llm.pricing.input_per_m` / `output_per_m` | `0.15` / `0.60` | USD per million tokens, used for cost tracking |
| `llm.stages.<stage>.model` / `max_tokens` / `extra_body` | `deepseek-flash` / `4096` / `{}` | Per-stage model settings (`synthesize_cluster`, `synthesize_score`) |
| `search.providers` | `[tavily, serper]` | Search providers, tried in order |
| `synth.max_signals` | `300` | Signals sent to the clustering call (calendar entries are always kept) |
| `radar.since_hours` | `72` | Look-back window for news and releases |
| `radar.lookahead_days` | `21` | How far ahead calendar events are listed |
| `radar.max_items` | `10` | Items taken per listing page |
| `radar.source_timeout_s` | `20` | Per-source timeout; a slow source fails alone |

## Sources

Radar sources live in `defaults/sources.yaml` (packaged) and your optional
`~/.kenya-data-engine/sources.yaml`. One self-contained entry per source, keyed by name:

```yaml
cbk_news:
  type: listing                  # rss | listing  (Google Trends is rss with kind: attention)
  url: https://www.centralbank.go.ke/news/
  kind: policy                   # news | data_release | policy | attention
  enabled: true                  # default true
  user_agent: "..."              # optional per-source User-Agent
  item: "article.post, div.news-item"   # listing only: CSS selectors; date may be null
  title: "h2.entry-title a, h3 a"
  link: "h2.entry-title a, h3 a"
  date: "span.entry-date, time"
  notes: "agent-sourced, unverified until `engine doctor` passes live"
```

Comma lists in selectors are intentional unions. A listing takes the first `<a>` with an `href`
inside each item and skips items with no title or link. A listing whose `item` selector matches
nothing fails with `selector matched nothing`: that is how layout drift shows up.

**Merge rule.** Your `sources.yaml` is merged over the packaged one **by name, field by field**.
So you can fix one selector (`cbk_news: {title: "h1 a"}`), switch a source off
(`nation: {enabled: false}`), or add a new source without copying the list. Every merged entry is
validated; an invalid one is reported as a failed source (in `engine doctor` and in run warnings)
and skipped. It never aborts the run, and neither do timeouts, empty results or bad selectors in
other sources. The calendar is its own adapter (`calendar.yaml`).

**Health.** After every run and probe, `engine.db` records per source: `last_ok_at`, `last_error`,
`consecutive_failures`, `last_signal_count`. `engine sources list` and `engine doctor` show it.

**Audit loop** (for people and for other agents):

1. `engine sources list --json` to find failing or never-run sources.
2. Edit `~/.kenya-data-engine/sources.yaml` (selector, URL, `enabled: false`, `notes`).
3. `engine sources test <name>`; repeat 2 and 3 until it extracts the right items.
4. Put the fix upstream in `src/kenya_data_engine/defaults/sources.yaml` and update `notes`
   with the verification date.

Sources marked "agent-sourced" in `notes` are unverified until they pass live.

HTTP identity: requests send a browser-like `User-Agent` (some Kenyan WAFs reject others) and an
`Accept` header; set `user_agent` on a source to override it (`reddit_kenya` ships with a
Reddit-friendly agent).

### Add a calendar entry

Edit `calendar.yaml`:

```yaml
events:
  - title: Budget statement
    rule: annual      # annual (month, day) | monthly (day, or -1 for last day) | date (ISO date)
    month: 6
    day: 12
    category: economy # economy | personal_finance | startups | business | law
    note: Optional context shown to the scorer
```

A malformed `calendar.yaml` shows up as a failed `calendar` source (in `engine run` and in
`engine doctor`) with a hint, instead of being silently ignored.

## Updating

```
uv tool install --reinstall git+https://github.com/TheAccountant777/simple-web-app@claude/loving-hawking-eucp75
```

Defaults (including the source list) ship with the engine, so an update brings new defaults
automatically, as long as your `config.yaml` only holds overrides. If you ran an older
`engine init`, your `config.yaml` is a full copy of the old defaults and pins them: run
`engine init --reset-config` (it saves the old file as `config.yaml.bak-<timestamp>`), then
re-apply any overrides you still want. `engine doctor` warns when it finds legacy `radar.*` source
keys. Your `sources.yaml` and `calendar.yaml` are never touched by an update.

## Troubleshooting

Every message has a `→` hint. Run `engine doctor` first; this maps each failure to a fix.

| Doctor says | Fix |
|---|---|
| `DEEPSEEK_API_KEY not set` | `engine init` (or export `DEEPSEEK_API_KEY`) |
| `no Tavily or Serper key` | `engine init` and add a Tavily or Serper key |
| `only Tavily is set` (warn) | Add a second search key for fallback; optional |
| `key rejected (HTTP 401)` | The DeepSeek key is wrong or revoked; `engine init` to replace it |
| `model not listed` (warn) | Change `llm.stages.*.model` in `config.yaml` to a model your account lists |
| `HTTP 5xx` / transport error on the LLM | DeepSeek or your network is down; retry later |
| Search fails | Check the Tavily/Serper key and quota; `search.providers` order |
| A source fails with `selector matched nothing` | The site changed; fix its selectors in `sources.yaml`, check with `engine sources test <name>` |
| A source fails with `incomplete certificate chain` | The server omits its intermediate certificate. The engine already tries to fetch it (kept in `certs/`) and retries once with verification on; if that fails too, it cannot be fetched automatically. TLS verification is never disabled |
| `config.yaml` warns about `radar.feeds` / `radar.enabled` (doctor) | Legacy keys no longer apply. Move entries to `sources.yaml` or run `engine init --reset-config` |
| A source shows `failing ×N` | It has failed N runs in a row; `engine sources test <name>` shows the error |
| A source fails with a transport error | The site is down or blocked; the run continues without it |
| A source returns `0 signals` (warn) | Empty feed or nothing in the window; raise `radar.since_hours` |
| An entry in `sources.yaml` is reported as `invalid source` | Fix the field named in the message |
| `calendar` fails with `invalid YAML` / `must contain an events: list` | Fix `calendar.yaml`, or `engine init --force` to restore the default |
| `invalid config` / `weights must sum to 1.0` | Fix `config.yaml` as the hint says |
| `run budget ... exhausted` | Raise `budgets.run_usd`; unscored clusters are skipped, not lost |

Unexpected errors print `✗ unexpected error: ...`; re-run with `-v` for a traceback.
Exit codes: `0` ok, `1` unexpected error or failed doctor check, `2` a user-facing error
(missing key, bad config, unknown run).

## Development

```
uv sync
make check     # ruff, ruff format --check, mypy, pytest with coverage >= 85%
```

Tests never touch the network (HTTP via `respx`, LLMs via Pydantic AI `FunctionModel`). Live
checks live only in `engine doctor` and `spikes/`.
