# Testing Phase 2 on your laptop

Phase 2 adds the data warehouse (`engine data`, `engine catalog`) and the research engine
(`engine research`, `engine dossiers`, `engine eval`). The cloud session can't reach Kenyan sites
and has no API keys, so these checks must run on your laptop. Each step says what "good" looks
like. If something fails, paste me the output.

## 0. Update

```
uv tool install --reinstall git+https://github.com/TheAccountant777/simple-web-app@claude/loving-hawking-eucp75
engine --version
engine doctor
```

## 1. Can DeepSeek run tool-using agents? (most important)

```
engine doctor --agents
```

**Good:** the agents row is ✓ with "tool use + structured output ok".
**If it fails:** paste the output. The research agents depend on this, and the spec has a
two-phase fallback I'll switch on.

## 2. The data warehouse

```
engine data list
engine data fetch wb:FP.CPI.TOTL.ZG      # Kenya inflation, annual %, World Bank API
engine data show wb:FP.CPI.TOTL.ZG --last 10
engine data fetch wb:FP.CPI.TOTL.ZG      # again: everything should be "unchanged"
engine data show wb:PA.NUS.FCRF --csv > kes_usd.csv   # after a fetch of that key
```

**Good:**
- the first fetch says *new N*, and the second says *unchanged N*;
- `show` prints years, values and source;
- the CSV opens in a spreadsheet.

## 3. Probe the Kenyan sources (needed for EPRA, CBK and KNBS parsers)

Every entry except the two World Bank series is **disabled until verified**. The probe tells us
which URLs work and saves real sample files so I can write the parsers.

```
git clone -b claude/loving-hawking-eucp75 https://github.com/TheAccountant777/simple-web-app
cd simple-web-app
engine catalog probe --all --save-samples tests/fixtures/real
git add tests/fixtures/real && git commit -m "real samples from catalog probe" && git push
```

**Good:** a table showing, for each entry, its status, final URL, links found and file type. A
lot of red is expected: those URLs were guesses. The `probe.json` and the samples are what I need.

## 4. Your first research dossier

```
engine run                     # fresh ranked topics
engine research 1              # research topic #1 from that run (standard budget)
engine dossiers list
engine dossiers show latest
```

Other ways to start one:
```
engine research "Central Bank of Kenya base rate decision"
engine research 2 --plan-only            # just the Planner's brief, very cheap
engine research 3 --budget lean
engine research --resume <run-id> 1      # continue a run that stopped
```

**Good:**
- live progress through the steps: plan → rounds → figures → claims → verify → dossier;
- a final panel with the verdict, facts / inferences / refused, the cost against $0.15, searches
  against 25, and the dossier path;
- `README.md` in the dossier has a "15-minute check" section. Do it once: open each top-fact link
  and confirm the quote is really there.

**Expect early on:** with only World Bank series enabled, most data needs go through the open web
path. That costs more searches and gives more *inference* than *fact*, which is honest. Once the
EPRA, CBK and KNBS parsers exist from your samples, fuel and rates topics will get Tier 1 facts
with no searches.

## 5. The scorecard

```
engine eval                    # runs the fuel, cbk_rate and weak scenarios (~$0.35 total)
engine report                  # now includes a Research section
```

**Good:**
- `weak` is rejected or reframed cheaply;
- `fuel` and `cbk_rate` produce dossiers within the caps;
- running `engine eval` again later compares against this run.

## What to send me
1. The output of `engine doctor --agents`.
2. The pushed `tests/fixtures/real/` (or the `probe.json` pasted).
3. One dossier folder (zip it, or paste `README.md` and `research/verification.md`).
4. The `engine eval` table.
