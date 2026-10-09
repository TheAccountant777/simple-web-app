# Kenya data catalog for the Data Scout (research, 2026-10-09)

## Honesty note: nothing here is VERIFIED
In this cloud session, WebFetch fails with `getaddrinfo ENOTFOUND` for centralbank.go.ke and
api.worldbank.org, and curl through the agent proxy gets `CONNECT 403`. Only WebSearch worked.
So **no page was fetched and no row below is VERIFIED**. Statuses used:
- **REPORTED-URL**: the exact URL appeared in a 2026-10-09 search result, so it is indexed. That is not proof it still serves.
- **REPORTED**: a fact from search snippets or secondary sources.
- **MEMORY**: the author's recollection, never seen in this session. Treat as a hypothesis.
- **UNKNOWN**: not found. Needs a laptop check. Do not guess URLs.

Every row needs a live check on the user's laptop (`curl -I` plus a parse test) before an adapter is written.

## Summary: top 10 series for chart-led personal-finance content
| # | Series | Best route found | Confidence |
|---|---|---|---|
| 1 | EPRA monthly max pump prices by town | EPRA PDF, 15th each month | URL pattern UNKNOWN. Press-quoted values only |
| 2 | KNBS CPI / inflation (headline, food, transport, housing) | KNBS monthly PDF, released the last day of the month | REPORTED |
| 3 | CBK CBR and MPC decisions | CBK press releases | UNKNOWN URL. Values reported in the news |
| 4 | T-bill/bond auction yields (91/182/364-day) | CBK Weekly Bulletin PDFs | REPORTED-URL |
| 5 | Interbank rate (KESONIA, official from 2025-09-01) | CBK Weekly Bulletin | REPORTED-URL |
| 6 | KES/USD daily indicative rate | CBK site; Frankfurter lists CBK as provider (KES from 2003) | REPORTED |
| 7 | Diaspora remittances (monthly) | CBK page (UNKNOWN URL); also in Weekly Bulletin | REPORTED |
| 8 | Fiscal outturn (QEBR) | Treasury QEBR PDFs | REPORTED-URL |
| 9 | Electricity bill pass-through charges (fuel energy cost, forex adjustment) | EPRA gazette notices, monthly | REPORTED, no official URL |
| 10 | Finance Bill status and amendments | Parliament PDFs; Mzalendo tracker as a cross-check | REPORTED |

Bonus for context charts: World Bank WDI via API (stable, machine-readable). CMA quarterly bulletin (NSE-20, bond turnover, inflation table).

## CBK (centralbank.go.ke)
Searches never surfaced the DataTables statistics pages. Do not assume they exist at the paths STATUS.md mentions.

| Series | URL | Format | Cadence | URL pattern | History | Status | Gotchas |
|---|---|---|---|---|---|---|---|
| Weekly Bulletin (carries interbank, T-bill/bond auctions, FX, reserves, remittances narrative) | `https://www.centralbank.go.ke/uploads/weekly_bulletin/<numeric-prefix>_Weekly CBK Bulletin <Month D, YYYY>.pdf`, e.g. `.../1411354329_Weekly CBK Bulletin January 16 2026.pdf` | PDF | Weekly, Fridays | **Not predictable**: random numeric prefix, spaces, inconsistent date and title forms (`Weekly Report - March 29, 2019`, `Februray`, `20221` typos). Discover by crawling the listing page, which is UNKNOWN. | Seen from 2019 to Jan 2026 | REPORTED-URL | Spaces in URLs need encoding. Tables are in PDFs, so pdfplumber. Typos break date parsing. |
| Interbank / KESONIA | in the bulletin above | PDF table | Daily values, weekly bulletin | n/a | n/a | REPORTED | The Jan-2026 bulletin says KESONIA became the official name on 2025-09-01. The series name changed, so splice it with the older interbank series. Week of 9-15 Jan 2026 averaged 8.99%. |
| T-bill auctions (91/182/364-day) | bulletin | PDF table | Weekly | n/a | n/a | REPORTED | Offered/bid/accepted amounts and average rates are tabulated. |
| Exchange rates (KES indicative) | `https://www.centralbank.go.ke/?p=5703` and `?p=8867` surfaced as centralbank.go.ke results, with an unconfirmed topic | HTML | Daily | `?p=<id>` ids are not stable semantics | UNKNOWN | REPORTED-URL | The homepage shows daily rates, CBR, KESONIA, 91-day bill and lending/deposit rates. Third party: Frankfurter lists CBK as a provider (KES from 2003, 22 currencies). It is open source, but it is not CBK, so cite CBK as the origin and check the licence. |
| CBR / MPC statements | UNKNOWN | HTML/PDF | About 6 per year | UNKNOWN | UNKNOWN | UNKNOWN | STATUS.md says the CBR was held at 8.75%. Do not rely on this. Verify via the CBK press release. |
| Inflation | CBK republishes KNBS. Use KNBS. | | | | | | |
| Diaspora remittances | UNKNOWN (bulletins have a monthly inflow table) | PDF/HTML | Monthly | UNKNOWN | UNKNOWN | REPORTED | Snippet: US was the top source (58% in Aug 2022). |
| Mobile payments | UNKNOWN | | Monthly | UNKNOWN | UNKNOWN | UNKNOWN | Not found. |

## KNBS (knbs.or.ke)
Known issue: `/recent-releases/` returns 404 (from the user's laptop run). The TLS chain needs AIA repair.

| Series | URL | Format | Cadence | URL pattern | History | Status | Gotchas |
|---|---|---|---|---|---|---|---|
| Advance Release Calendar FY2025/26 | `https://www.knbs.or.ke/wp-content/uploads/2025/11/ADVANCE-RELEASE-CALENDAR-FY-2025-2026-1.pdf` | PDF | Annual | WordPress `wp-content/uploads/YYYY/MM/` | n/a | REPORTED-URL | Use it to schedule fetches. The FY2026/27 edition is UNKNOWN. |
| CPI and inflation | UNKNOWN (WordPress uploads, likely `wp-content/uploads/YYYY/MM/<Title>.pdf`, MEMORY) | PDF | Monthly, last day of the month (per calendar) | Unconfirmed | UNKNOWN | REPORTED | Press reports: Aug 2026 6.6%, Jul 6.5%, May 6.7%, Jan 4.4%, Dec 2025 4.5%. Sources disagree on the base month for May. Take figures from the PDF only. |
| Leading Economic Indicators | UNKNOWN | PDF | Monthly, about 2-month lag (per calendar) | UNKNOWN | UNKNOWN | REPORTED | Not found. |
| Economic Survey, quarterly GDP | UNKNOWN | PDF | Annual / quarterly | UNKNOWN | UNKNOWN | UNKNOWN | Not searched. |
| open data portal (opendata.go.ke) | `opendata.go.ke` | | | | | REPORTED | Conflicting: a catalogue lists it as active, while an undated OGP page says it has been offline for years. Assume dead until `curl` proves otherwise. |

## EPRA (epra.go.ke)
| Series | URL | Format | Cadence | Pattern | History | Status | Gotchas |
|---|---|---|---|---|---|---|---|
| Max retail pump prices | PDF titled "Maximum retail petroleum prices for the period 15th April – 14th May 2026", Annex I = all towns. **Exact URL not found.** | PDF | Monthly, effective the 15th | UNKNOWN | UNKNOWN | REPORTED | Press-reported Nairobi: Jul 15-Aug 14 2026 Super 214.03, Diesel 222.86, Kerosene 191.38 (unchanged). April rise was Super +28.69, Diesel +40.30. Press values are cross-checks only. Find the archive URL on the laptop. |
| Electricity pass-through (FEC, forex adj, water levy) | gazette notices, no URL found | Gazette PDF | Monthly | UNKNOWN | UNKNOWN | REPORTED | Unit confusion (cents vs shillings per kWh) in press. Use the gazette. |
| Retail tariff review 2026/27-2028/29 | UNKNOWN | | | | | REPORTED | Kenya Power application under public consultation. No final decision seen. |

## National Treasury
| Series | URL | Format | Cadence | Pattern | History | Status | Gotchas |
|---|---|---|---|---|---|---|---|
| QEBR | `https://treasury.go.ke/sites/default/files/QEBRs/4th%20QEBR%20Report%202025-2026.pdf`; `.../QEBRs/Third%20QEBR%20in%202025-26%20FY%2013.05.2026%20(2).pdf`; `https://www.treasury.go.ke/wp-content/uploads/2025/05/Third-QEBR-in-2024-25-FY-Finalpdf.pdf` | PDF | Quarterly (Q4 FY25/26 dated Aug 2026) | **Inconsistent** across hosts (`nt.`, `newsite.`, `www.`), paths, and names | FY2016/17 onward | REPORTED-URL | Large PDFs. Filenames include dates and "(2)". Crawl the listing. |
| Budget statement, public debt bulletin, Finance Bill | not found | | | | | UNKNOWN | |

## Parliament and Kenya Law
| Series | URL | Format | Cadence | Pattern | History | Status | Gotchas |
|---|---|---|---|---|---|---|---|
| Finance Bill 2026 (National Assembly Bill No. 26 of 2026, published 5 May 2026) | Mzalendo tracker `https://mzalendo.com/legislative-trends/bills/na/422/` | HTML | Event-driven | Bill id `na/<n>` | Per bill | REPORTED-URL | Tracker last updated 22 June 2026 at "Committee of the whole House". It is third party. Primary status needs the parliament.go.ke bills page and the Hansard (UNKNOWN URLs). Reported effective date 1 July 2026 if passed. |
| Gazette | `https://new.kenyalaw.org/gazettes/`; issue pages `https://new.kenyalaw.org/akn/ke/officialGazette/<YYYY-MM-DD>/<issue-no>/eng@<YYYY-MM-DD>` | HTML plus PDF link | Weekly plus specials | **Templated by date**, but the issue number must be discovered | 1901 to present (per a third party) | REPORTED-URL | No documented API. A feed at `/feeds/all.xml` is claimed by a third-party catalogue. Unconfirmed. Possible JS rendering is unknown. |
| Acts | `new.kenyalaw.org` (AKN scheme) | | | | | REPORTED | Not detailed. |

## CMA and NSE
| Series | URL | Format | Cadence | Pattern | History | Status | Gotchas |
|---|---|---|---|---|---|---|---|
| CMA Quarterly Statistical Bulletin | `https://www.cma.or.ke/download/85/2026/6390/capital-markets-authority-cma-quarterly-statistical-bulletin-q2-2026.pdf` (also Q3-2025 `.../download/75/2025/5853/...`) | PDF | Quarterly | `/download/<cat>/<year>/<id>/<slug>.pdf`: **ids are not predictable** | 2021 to Q2 2026 seen | REPORTED-URL | Q4 2025 lives on another host (`cmarcp.or.ke/images/bulletin/...`). Reuse is allowed with acknowledgement (per 2022 bulletin). Has inflation, NSE-20, bond and IPO tables. |
| NSE market stats | UNKNOWN | | | | | UNKNOWN | Not found. |

## International
| Series | URL | Format | Cadence | Status | Gotchas |
|---|---|---|---|---|---|
| World Bank WDI | `https://api.worldbank.org/v2/country/KEN/indicator/<CODE>?format=json&per_page=N` (e.g. `FP.CPI.TOTL.ZG`); official docs: datahelpdesk.worldbank.org/knowledgebase/articles/898581 | JSON (page 1 is metadata, page 2 data) | Annual, revised | REPORTED (docs surfaced; endpoint not fetched) | Values get revised, so store the fetch date. Multiple indicators can be joined with `;`. The proxy blocked it here. |
| IMF | New portal `data.imf.org` (SDMX 3.0 REST). Legacy portal retired 2025-11-05. `dataservices.imf.org` reportedly decommissioned in 2025. | SDMX | n/a | REPORTED | IFS was restructured and the old codes no longer resolve, with no official crosswalk. Keys are dot-delimited, e.g. `GBR.NGDP_RPCH.A`. About 10 requests per 5 s (third-party claim). The `api.imf.org` host is unconfirmed. Re-derive codes through the SDMX dataflow and structure endpoints on the laptop. |
| FAOSTAT | bulk service (Consumer Price Indices domain listed in `datasets_E.json`, per ReliefWeb) | CSV zip | Monthly | REPORTED | The exact file URL is UNKNOWN. |
| Aggregators | tradingeconomics: paywalled, licence forbids redistribution, so do not scrape. allratestoday.com: commercial, paid downloads, history from 2024. Frankfurter: free API, lists CBK. Apify actor "Kenya CBK Rates": scraper, no licence guarantee. Mzalendo: third-party legislative tracker (open). | | | REPORTED | Use only as cross-checks and cite primary sources. |

## Recommended adapter types
**Custom code adapters (stable APIs and discoverable listings):**
1. World Bank JSON adapter (simple, stable).
2. IMF SDMX adapter (rewrite for the new portal; fragile).
3. Frankfurter JSON, as a cross-check for KES.
4. Kenya Law Gazette adapter (AKN date-templated URLs plus a listing crawl).

**Listing-crawl plus PDF discovery (URLs unpredictable, so never template):**
5. CBK Weekly Bulletin, EPRA price schedules, KNBS monthly releases, Treasury QEBR, CMA bulletins. Pattern: fetch the listing HTML, regex the PDF links, download, and extract with pdfplumber. Fall back to Docling for complex tables.

**Generic extractors:** HTML table (CBK homepage rates; the DataTables pages need a JS check), PDF tables (pdfplumber), CSV/XLSX (FAOSTAT bulk).

**Rules for the Data Scout:** never construct PDF URLs from dates. Always cross-check PDF values against the press-reported figures above. Treat 404s and 403s as soft failures with a recorded hint.

## Next steps (on the user's laptop)
`curl -I` the listing pages (CBK statistics, EPRA downloads, KNBS publications, Treasury, Parliament bills), then record real URLs and mark them VERIFIED with a date.
