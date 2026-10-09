# Table extraction for Kenyan government PDFs and spreadsheets

_2026-10-09. Research note, not a decision. Tags: **[src]** = from a source fetched in this research (linked);
**[inf]** = my inference or background knowledge, not verified here; verify before relying on it._

Caveat up front: web search returned no independent benchmark on financial tables, and nothing on Kenyan
government PDFs. The only rigorous comparison found is on scientific PDFs. **Run a bake-off on 10-20 real EPRA/CBK/KNBS/Treasury
files on the laptop before committing** (see §6).

## 1. Recommendation (tiered strategy)

Keep the core install light. Everything heavy is an optional extra and the engine degrades to the next tier.

| Tier | Tool | When | Install |
|---|---|---|---|
| 0 | **openpyxl / pandas** for XLSX/CSV | Source offers a spreadsheet. Always prefer it over the PDF. | core (add `openpyxl`, `pandas` or stay with plain openpyxl) |
| 1 | **pdfplumber** (already a dep), tuned per source with explicit `table_settings` and, for borderless tables, word-position/column-x clustering | Digital (text-layer) PDFs with a stable layout, e.g. EPRA price schedules | core |
| 2 | **PyMuPDF `find_tables()`** as an independent second extractor for cross-checking Tier 1 | Cross-check only | extra `tables-fast`. **AGPL-3.0** [src]: fine for a personal local tool [inf], a problem if the engine is ever distributed or hosted closed-source. |
| 3 | **Docling** (TableFormer) | Tier 1 fails verification, or the layout is borderless/irregular | extra `tables-ml` (pulls torch; see §3) |
| 4 | **OCR** (OCRmyPDF + system Tesseract, or Docling OCR) | No text layer (scans) | extra `ocr`; needs system packages |

Rules (inferred from the project's principles, not sourced):
- Per-source parsers in code (a `ParserSpec` per EPRA/CBK/KNBS/Treasury series) beat generic extraction for recurring
  publications. Generic extractors are the fallback and the cross-check.
- A table is **accepted only if it passes the verification checklist (§4)**. If Tier 1 fails, escalate one tier, then
  fail soft: emit no numbers, log loudly, keep the artifact.
- Never let an LLM read cells. An LLM may at most help *write/propose* a parser spec, which a human reviews.
- Do not add Camelot or Tabula now (reasons in §2). Do not add Marker or unstructured (heavier, no clear gain here [inf]).

## 2. Comparison

| Tool | Bordered | Borderless | Weight | CPU speed | Licence | Maintenance |
|---|---|---|---|---|---|---|
| **pdfplumber** | Good [inf] | Weak by default; needs tuning. A June 2026 blog claims ~55-65% on complex financial tables, methodology unclear [src: [thedrive](https://dev.thedrive.ai/blog/extract-tables-from-pdf-2026)] | Pure Python (pdfminer.six) [inf] | Fast [inf] | MIT [inf] | Active [inf] |
| **Camelot** | Strong; docs claim best on multi-row headers/merged cells (vendor view) [src: [Camelot comparison](https://camelot-py.readthedocs.io/en/latest/user/comparison.html)] | "Stream" mode, fragile [inf] | Needs Ghostscript and OpenCV [src: [Ubuntu pkg](https://packages.ubuntu.com/nl/noble/camelot); [issue #13](https://github.com/camelot-dev/camelot/issues/13)] | Moderate [inf] | MIT [inf] | Could not confirm 2025-26 status |
| **Docling** | Best in the one rigorous benchmark found, but on scientific PDFs; PyMuPDF/pdfplumber scored far lower there [src: [arXiv 2511.16134](https://arxiv.org/pdf/2511.16134)] | Same benchmark; ML layout model suits borderless tables [inf] | torch is a hard dep; layout + TableFormer models ~506 MiB on disk (one reviewer's measurement) [src: [Thunderbit review](https://thunderbit.com/ko/blog/docling-review)]; models download on first run | Median 0.79 s/page x86 CPU (range 0.6-16.3 s), 1.26 s/page on M3 Max; TableFormer ~1.74 s/table [src: [Docling paper](https://arxiv.org/pdf/2501.17887)] | MIT [inf] | Very active (IBM / LF AI) [inf] |
| **PyMuPDF** `find_tables` | Default strategy "lines" uses vector rulings [src: [PyMuPDF blog](https://medium.com/@pymupdf/solving-common-issues-with-table-detection-and-extraction-4df5de2b8d88)] | May miss tables, merge/split columns, split multi-line cells; many tuning params [src: same] | Single wheel, no torch [inf] | Very fast [inf] | **AGPL-3.0**, commercial option [src: [PyMuPDF repo](https://github.com/pymupdf/PyMuPDF)] | Active |
| **pymupdf4llm** | Wraps PyMuPDF, outputs Markdown | Same limits | Light [inf] | Fast [inf] | Not confirmed; assume AGPL like PyMuPDF [inf] | Active |
| **Tabula (tabula-py)** | OK [inf] | Weak [inf] | Needs a Java runtime [inf] | Slow JVM start [inf] | MIT [inf] | Low activity [inf] |
| **Marker** | Not searched | Not searched | torch + models [inf] | Slow on CPU [inf] | Not verified (GPL code / restricted weights suspected) | Not verified |
| **unstructured** | Not searched | Not searched | Heavy extras [inf] | Slow [inf] | Apache-2.0 [inf] | Not verified |

Bottom line [inf]: Camelot's Ghostscript/OpenCV dependency and uncertain upkeep make it a poor fit; Tabula adds a JVM; Docling is the
only ML option worth the weight; PyMuPDF is the best cheap second opinion if AGPL is acceptable for a local tool.
Neither Docling's quality on financial tables nor multi-page table handling is established; blog sources say multi-page tables are a weak
point for all tools [src: thedrive, above].

## 3. Docling on a laptop

- Speed: roughly 0.8-1.3 s/page on CPU per the paper [src], plus the first-run model download (one reviewer saw ~224 s) [src: Thunderbit].
- RAM: one summary says within 7 GB [src: [emergentmind](https://www.emergentmind.com/topics/docling)], low-quality source.
- torch is the real cost: ~hundreds of MB to GB of wheels [inf]. Install the CPU-only torch wheel index where possible [inf; one blog suggests it,
  unverified]. Keep it in an extra, import lazily, and cache models outside `runs/`.
- Use it only on pages where Tier 1 fails verification, not on every PDF (a 3-6 page EPRA schedule is ~5 s [inf]).

## 4. Scanned PDFs / OCR

- Docling supports Tesseract, EasyOCR, RapidOCR (onnxruntime backend default) and macOS Vision; auto-selection order is EasyOCR, Tesseract, RapidOCR,
  Vision [src: [Docling OCR docs](https://docling-project.github.io/docling/concepts/OCR/)]. Tesseract needs a system install [src: codesota mirror].
  EasyOCR is torch-based and GPU-oriented [src] so avoid on laptops; **pin the engine explicitly** (Tesseract or RapidOCR) [inf].
- OCRmyPDF defaults to Tesseract and adds a text layer to the PDF, so Tier 1 can then run on it [src: [pypi](https://pypi.org/p/ocrmypdf-easyocr)]; licence MPL-2.0 [src: [JabRef ADR](https://devdocs.jabref.org/decisions/0056-OCR-engine-selection.html)].
  Tesseract is Apache-2.0 [inf, unverified].
- **OCR digits are the riskiest input.** OCR-derived tables must pass stricter checks (two engines agree, or totals reconcile) and be flagged
  `ocr=true` in provenance [inf]. Prefer asking the publisher for the XLSX or finding a text-layer copy.

## 5. Verification checklist (apply per table; all inferred from practice, no source found on newsroom/OWID specifics)

1. **Schema**: expected header labels present (exact match after normalisation); expected column count; row labels from a known list (e.g. towns for EPRA: Nairobi, Mombasa, Nakuru, Kisumu, Eldoret).
2. **Types**: every numeric cell parses as a decimal; strip thousands separators and footnote marks; reject `O`/`l` for `0`/`1` look-alikes (OCR).
3. **Range checks**: per-series plausible band (pump prices KES 100-400/L; month-on-month change below e.g. 15%; CPI index within +-X of prior print). Violations quarantine, not clamp.
4. **Internal consistency**: stated totals vs recomputed sums; subtotals; shares sum to ~100; EPRA invariants such as Super > Diesel > Kerosene ordering is *not* guaranteed, so check against history instead. Compute with `Decimal`.
5. **Cross-extractor agreement**: run Tier 1 and Tier 2 (or 3); compare cell by cell on normalised text. Any disagreement blocks publish and goes to a diff report.
6. **Cross-source check**: same figure reported in the press release/HTML page, or an earlier release that restates it (CBK bulletin vs CBK data portal, KNBS CPI vs EPRA-linked inflation text).
7. **Historical continuity**: compare against the last stored value for the same key; large jumps need a corroborating quote.
8. **Completeness**: row/cell count equals the previous release's (a dropped row is a silent failure).
9. **Provenance present**: page number, bbox, extractor+version for every number (§7).
10. **Visual audit sample**: render the cropped page region to PNG next to the extracted cell for human spot-checks on first use of a new parser.

## 6. Bake-off plan (do before adopting Tier 2/3)

On the laptop (Kenyan sites are proxy-blocked in the cloud): collect the last 6 EPRA schedules, 2 CBK bulletins, 2 KNBS CPI PDFs, 1 Treasury table.
Hand-key ground truth into JSON fixtures; score exact-cell-match for pdfplumber (tuned), PyMuPDF, Docling; record time and RAM.
Commit only small redistributable fixtures, since sources may carry copyright. Tests must stay offline.

## 7. XLSX / CSV gotchas and idioms

- Merged header cells: read-only openpyxl does not expose merged ranges (only the top-left cell has a value) [src: [openpyxl issue 1302](https://foss.heptapod.net/openpyxl/openpyxl/-/issues/1302), old version 2.5.8]. Load normally (not `read_only`) and forward-fill
  across `ws.merged_cells.ranges` explicitly [inf].
- Title/logo rows above the header make `header=0` mis-shift data [src: askpython-class posts; low quality]. Locate the header row by content search
  (first row containing the expected labels), then slice [inf].
- Multi-row headers: `header=[0,1]` then flatten tuples, or build keys yourself from forward-filled rows [src, low quality].
- Footnote/total rows: stop at the first all-empty row or a row whose first cell matches `^(Note|Source|\*|Total)`; keep them as separate `notes` rather than discarding [inf].
- Hidden rows/columns and `#REF!`/formula cells: open with `data_only=True` to get cached values, and warn if a cached value is `None` [inf].
- Always `dtype=str`/`Decimal` on read, then convert in code; avoid float-introduced noise; dates may be Excel serials or text [inf].
- CSV: detect encoding (`utf-8-sig`, cp1252), BOM, and `;` delimiters [inf].
- pandas vs polars: no source found. For files of <10k rows, pandas (or plain openpyxl) is adequate; polars adds a dependency with no benefit
  at this scale, and its Excel reading goes through other engines anyway [inf]. **Recommend openpyxl + stdlib `csv` + optional pandas only if convenient.**

## 8. Provenance schema proposal

Model at two levels: a **document** record (one per fetched file) and a **cell** record (one per number). Loosely aligned with W3C PROV-DM
concepts (entity, activity, derivation) [src: [PROV-DM](https://www.w3.org/TR/prov-dm/)] without adopting the library; JSON in `runs/<id>/` as artifacts.

```jsonc
// SourceDocument
{
  "doc_id": "sha256:<hex>",            // content hash is the identity
  "source_name": "epra_pump_prices",    // key in defaults/sources.yaml
  "url": "https://...",                 // requested URL (redacted via Tracer.redact)
  "final_url": "https://...",           // after redirects
  "retrieved_at": "2026-10-09T08:15:00Z",
  "http_status": 200, "content_type": "application/pdf",
  "etag": null, "last_modified": "…",
  "bytes": 123456, "sha256": "<hex>",
  "local_path": "runs/<id>/raw/<sha256>.pdf",   // stored raw copy
  "publisher_period": "2026-09",        // period the doc claims, parsed in code
  "has_text_layer": true, "ocr": null   // or {"engine":"tesseract","version":"…"}
}
// ExtractedCell
{
  "cell_id": "<doc_id>#p3.t1.r4.c2",
  "doc_id": "sha256:<hex>",
  "page": 3,                            // 1-based, PDF page index
  "table_index": 1, "row": 4, "col": 2,
  "bbox": [x0, top, x1, bottom],        // PDF points, page origin stated; null for XLSX
  "sheet": null, "cell_ref": null,      // XLSX: "Sheet1!C12"
  "raw_text": "KSh 206.97",             // exactly as extracted
  "value": "206.97", "unit": "KES/L",   // Decimal as string
  "row_label": "Nairobi", "col_label": "Super Petrol",
  "extractor": {"name": "pdfplumber", "version": "0.11.x", "settings_hash": "<hex>"},
  "cross_check": {"extractor": "pymupdf", "agree": true},
  "checks": {"schema": "pass", "range": "pass", "sum": "n/a", "history": "pass"},
  "status": "accepted"                  // accepted | quarantined | rejected
}
```
Audit aid [inf]: render `page` cropped to `bbox` into `runs/<id>/evidence/<cell_id>.png` so an agent or human can see the number's source.
Grounding quotes (`tools/grounding.py`) should quote `raw_text` plus `row_label`/`col_label`, and claims reference `cell_id`s.
Store `settings_hash` so a parser-config change is detectable; bump a `parser_version` when specs change.

## 9. Open questions

- Licensing of PyMuPDF if the engine is ever packaged/distributed (AGPL).
- Docling real accuracy and RAM on EPRA schedules: unmeasured until the bake-off.
- Marker, unstructured, Camelot 2025-26 maintenance, pandas-vs-polars: not verified by sources here.
- How OWID and statistical agencies verify scraped tables: no source found; §5 is general practice, not a documented standard.
