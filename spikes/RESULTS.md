# Spike results

## Spike 1: LLM extra_body

Not run — no DEEPSEEK key in session; defaults use `extra_body: {}`.

## Spike 3: source access

Not run live — proxy blocked (the build container's proxy rejects Kenyan sites with CONNECT 403).
Fixtures are hand-built from known page structures; URLs and selectors are unverified until
`engine doctor` runs live.

To verify on a laptop: `uv run python spikes/sources.py` (add `--save` to replace the
hand-built fixtures with real captures), then fix URLs/selectors in
`src/kenya_data_engine/defaults/config.yaml`.

| Source | Kind | URL (unverified) | Selectors (unverified) | Status |
|---|---|---|---|---|
| Nation, Standard, Business Daily, The Star, Kenyans.co.ke, Capital FM | RSS | see `radar.feeds` | n/a | unverified |
| Google Trends KE | RSS | `https://trends.google.com/trending/rss?geo=KE` | n/a | unverified |
| CBK press releases | listing | `radar.listings.cbk` | item `article`, title/link `h2 a`, date `time` | unverified |
| KNBS publications | listing | `radar.listings.knbs` | item `article`, title/link `h2 a`, date `time` | unverified |
| EPRA press releases | listing | `radar.listings.epra` | item `article`, title/link `h2 a`, date `time` | unverified |
| Parliament bills | listing | `radar.listings.parliament` | item `table tbody tr`, title/link `td a`, date `td:nth-child(2)` | unverified |

No adapter is marked `deferred`.
