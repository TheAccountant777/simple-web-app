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

Live health checks, grouped: API keys, the LLM (`GET /models`), web search (one query), and each
enabled Radar source. Exits 1 if any check fails.

```
engine doctor
engine doctor --json
```

### `engine init`

Creates the home, copies `config.yaml` and `calendar.yaml` if absent, asks for your keys, verifies
the DeepSeek key, then runs doctor.

```
engine init                  # interactive; Enter keeps a saved key
engine init --force         # also overwrite config.yaml and calendar.yaml
engine init --no-verify     # skip the key check and doctor (offline)
DEEPSEEK_API_KEY=... TAVILY_API_KEY=... engine init --non-interactive
```

## Where things live

Default home: `~/.kenya-data-engine/` (override with `--home` or `ENGINE_HOME`).

```
config.yaml   calendar.yaml   .env   engine.db   runs/<YYYY-MM-DD-HHMM>/   briefs/
```

Each run directory holds `signals.json` (Radar), `topics.json` (Synthesize), `trace.jsonl` (every
span and LLM call with tokens and cost) and `summary.json`. A truncated or corrupt artifact counts
as missing, so `--resume` simply re-runs that stage.

## Configuration

`~/.kenya-data-engine/config.yaml` is deep-merged over the packaged defaults, so you only write
the keys you want to change.

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
| `radar.feeds` | six Kenyan outlets | Name to RSS URL map |
| `radar.trends_feed` | Google Trends KE | RSS URL for search attention |
| `radar.listings` | CBK, KNBS, EPRA, Parliament | Name to listing-page spec (CSS selectors) |
| `radar.enabled` | all of the above plus `calendar` | Which sources run |

### Add an RSS feed

```yaml
radar:
  feeds:
    my_outlet: https://example.co.ke/rss
```

Maps are deep-merged with the defaults, so your feed runs alongside the built-in ones as long as
`rss` is in `radar.enabled`. Lists such as `radar.enabled` are replaced, not merged: to run only
some sources, write the full list you want (for example `enabled: [calendar, cbk]`).

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

### Fix a listing page

If a site changes its layout, its listing check fails with "selector matched nothing". Adjust
`radar.listings.<name>` (`item`, `title`, `link`, `date` are CSS selectors) in `config.yaml`.

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
| A source fails with `selector matched nothing` | The site changed; update `radar.listings.<name>` selectors |
| A source fails with a transport error | The site is down or blocked; the run continues without it |
| A source returns `0 signals` (warn) | Empty feed or nothing in the window; raise `radar.since_hours` |
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
