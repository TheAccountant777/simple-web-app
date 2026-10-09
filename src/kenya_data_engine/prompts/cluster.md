{{primer}}

## Task
You receive a list of signals, one per line, in the form
`id | kind | source | date | title`.
Group signals about the same underlying story or theme into clusters.
- One signal can be in at most one cluster.
- Ignore signals that have no plausible money, economy, business, startup or law angle.
- Give each cluster a short `title`, a one or two sentence `summary`, a `category` and the
  `signal_ids` it contains.
- `category` must be exactly one of: economy, personal_finance, startups, business, law.
- Use only the ids given; never invent ids.

## Worked example
Signals:
```
a1 | news | business_daily | 2026-10-07 | EPRA raises petrol price by KES 4 for October
b2 | news | the_star | 2026-10-07 | Matatu fares up as fuel prices climb
c3 | news | nation | 2026-10-08 | Safaricom unveils new M-Pesa fees
```
Expected output:
```json
{"clusters": [
  {"title": "October fuel price rise hits transport costs",
   "summary": "EPRA raised pump prices and matatu fares followed.",
   "category": "personal_finance", "signal_ids": ["a1", "b2"]},
  {"title": "New M-Pesa fee schedule",
   "summary": "Safaricom changed its M-Pesa tariffs.",
   "category": "business", "signal_ids": ["c3"]}
]}
```
