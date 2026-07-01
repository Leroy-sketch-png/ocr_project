# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — V3 branch · 2026-07-01

This branch represents a full-day engineering sprint building the OCR
pipeline from scaffolding to a reference-suite-validated, defensively
correct extraction system.

---

### Wave 0 — Core scaffolding (morning)
**Commits:** `8ab1f75`, `f4d5288`, `ed71839`

- Built the full pipeline entry point (`src/main.py`) connecting OCR engine →
  table builder → field extractor → repair engine → validator → exporter
- Implemented multi-phase math repair engine (`src/repair_engine.py`) with
  Phase 1 digit mutation, Phase 2 sniper OCR, Phase 3 inverse token search
- Added evaluation harness (`tests/evaluate.py`) against `tests/ground_truth.json`
  covering 57 fields across 3 reference documents

---

### Wave 1 — Confidence scoring + 3 ground-truth mismatches fixed
**Commits:** `34b2e11`, `53ff089`

#### Added
- Confidence levels on every output field: `high` (direct keyword match),
  `medium` (repair or column selection), `low` (inferred/nulled),
  `inferred` (inverse search). Emitted in JSON output so downstream
  systems can route by confidence.
- New accounting equation: `Profit/Loss Before Tax = Net Profit/Loss + Income Tax Expense`
- `Income Tax Expense` field added to `field_config.yaml`
- `1 ↔ 4` bidirectional entry in `CONFUSION_SET` (common OCR font confusion)

#### Fixed
- **SAMPLE2 Cost of Sales false positive:** `_apply_null_cos_without_gp()` —
  if no Gross Profit line exists, Cost of Sales is nulled. A services-only
  P&L has no COGS concept; the keyword match was a false positive.
- **SAMPLE2 PBT digit confusion:** OCR read `14,095,953` instead of
  `11,095,953`. Fixed via `1↔4` CONFUSION_SET entry + PBT equation anchor.
- **SAMPLE3 Current/Non-Current Liabilities wrong:** `zero_ncl_inference`
  was firing before inverse search could run. Added `Borrowings` keyword
  to Non-Current Liabilities config; NCL now correctly extracted as `1,463`.

---

### Wave 2 — Robustness hardening for unknown documents
**Commits:** `786d5f5`, `75c8228`, `83af225`

#### Added
- **Dynamic column-pitch threshold** (`field_extractor.py`): previously
  hardcoded at 150px — breaks on any document with different DPI or column
  spacing. `detect_year_column()` now computes inter-column gap from actual
  header positions and uses `0.55 × col_pitch` as the cell selection
  threshold. Falls back to `200 × dpi_scale` for single-year documents.
- **DPI-normalised split-token merge geometry** (`repair_engine.py`):
  `y_overlap < 15px` and `x_gap < 60px` were fixed pixel values. Both now
  scale with `dpi_scale` so behaviour is consistent across 150/300 DPI scans.
- **Liabilities closure sanity check** (`_check_liabilities_closure`):
  post-repair verification that `TL == CL + NCL`. Emits `[LIAB-CLOSURE]`
  WARNING and downgrades TL to `confidence=low` when the balance sheet
  does not close. Diagnostic only — does not mutate values.
- **Repair chain cap** (`_MAX_REPAIRS_PER_FIELD = 1`): a field may only be
  mutated once per repair run. Prevents equation cross-contamination where
  fixing field A for equation 1 silently breaks equation 2.
- **Structural NCL candidate guard** in `zero_ncl_inference`: suppressed
  if any row scores ≥ 60 against NCL keywords. Prevents silent `CL = TL`
  corruption when an NCL row exists but didn't match at the normal threshold.
- **API hardening** (`api.py`): `optimization_mode=True` default, 20 MB file
  size guard, currency extracted from column headers and emitted as top-level
  field in output JSON.
- **Evaluator fix:** false-positive extractions (`extracted != None`,
  `expected == None`) now count as mismatches instead of being silently skipped.

---

### Wave 3 — Critical structural bugs (pipeline was silently broken)
**Commit:** `bac591f`

These bugs meant the pipeline appeared to work but several core protections
were completely inactive.

#### Fixed
- **CRITICAL: `detect_page_sections()` signature broken** (`table_builder.py`):
  Function signature was `detect_page_sections(blocks: List[TextBlock])`.
  `main.py` called it as `detect_page_sections(table_rows, text_blocks)` —
  two arguments. Python silently bound `table_rows` to `blocks` and ignored
  `text_blocks` entirely. TableRow objects don't have `.tokens` in the same
  shape as TextBlocks, so every page resolved to `section='unknown'`.
  **Result: the entire section guard in Phase 1.6 and Phase 3 was a
  silent no-op. All section-based false-positive protection was dead.**
  Fix: signature now correctly accepts both `table_rows` AND `text_blocks`;
  uses `text_blocks` for section marker detection and `table_rows` for
  forward-filling sections across data pages.
- **CRITICAL: `export_fields()` never existed** (`exporter.py`):
  `main.py` called `export_fields(validated)` but `exporter.py` only
  defined `field_value_to_dict()`. Every production run would `NameError`.
  Fix: added `export_fields()` that iterates all fields and calls
  `field_value_to_dict` per field.
- **`validate_fields()` signature mismatch** (`validator.py`):
  `main.py` called `validate_fields(repaired)` with one argument;
  the function required two (`field_values` + `required_fields`).
  Fix: `required_fields` is now optional (defaults to `[]`).
- **Section map propagation:** `page_section_map` now correctly built from
  `text_blocks` (not `table_rows`) and verified threaded through to
  `apply_math_repairs()` unchanged.

---

### Wave 4 — Logic bugs in section classifier and repair engine
**Commit:** `00ecb63`

#### Fixed
- **BUG 1 — bare `"notes"` marker** (`table_builder.py`):
  The `SECTION_MARKERS["notes"]` list contained `"notes"` as a bare
  single-word entry. Combined with the notes-lock (`once notes, never leave`),
  any line containing the word "notes" — footnote references, `"notes
  payable"`, `"see notes 3 and 4"` — would permanently freeze the entire
  document into the notes section. The section guard passthrough
  (`tok_section == "notes"` allows any token) would then become useless.
  **Fix:** removed the bare `"notes"` entry. Only four specific multi-word
  headers remain (`"notes to the financial statements"`, etc.).
- **BUG 2 — TOC regex drops real financial rows** (`table_builder.py`):
  `re.search(r'\s+\d+$', line_text.strip())` matched any line ending in
  digits after whitespace — including real data rows like `"Total Assets 5"`.
  **Fix:** replaced with `_TOC_LINE_RE = re.compile(r'^.{15,}\s{2,}(\d{1,3})\s*$')`
  which requires ≥15 chars of content, ≥2 spaces before the number, and a
  1–3 digit page number (1–999). Financial values in thousands-scale reports
  are always ≥1,000; TOC page numbers are always ≤999. The gap is clean.
- **BUG 3 — dead `_apply_opl_anchor()` function with lying docstring**
  (`repair_engine.py`): The hand removed the call during local debugging
  but left the 30-line function body in place. The `apply_math_repairs()`
  docstring still listed `0C — OPL anchor from PBT` as an active pre-phase.
  **Fix:** function body deleted, docstring corrected to list only `0A` and
  `0B` (the two pre-phases that actually run).
- **BUG 4 — NCL guard comment said 60, code ran 80** (`repair_engine.py`):
  Comment read `scores >= 60` after threshold was raised to 80 during hand
  debug. **Fix:** comment aligned to reality; explains why 80 was chosen.
- **BUG 5 — dead `Set` import** (`repair_engine.py`):
  `Set` imported from `typing`, never used. Removed.

---

## Reference Suite Results (as of wave-4)

| Document | Fields | Correct | Score |
|---|---|---|---|
| AA_SAMPLE1 (manufacturing conglomerate) | 19 | 19 | 100% |
| AA_SAMPLE2 (Norwegian investment firm) | 19 | 19 | 100% |
| AA_SAMPLE3 (services company) | 19 | 19 | 100% |
| **Total** | **57** | **57** | **100%** |

**Important caveat:** 100% on 3 known reference documents is not the same
as production accuracy. The smoke test on an 85-page Apple 10-K correctly
returned `null / valid: false` for non-standard fields rather than
hallucinating values. No false-confident wrong numbers were produced.

---

## Known Limitations (to address in V4)

1. **Notes-page passthrough is broad:** tokens on any `notes`-classified
   page are allowed to match any field. Intentional for `Income Tax Expense`
   but could produce false positives on documents with very dense notes
   sections containing historical comparative figures.
2. **Section classifier is text-only:** relies on OCR text headers. Documents
   where section headers are images (scanned logos, styled text rendered as
   bitmap) will not be classified. All pages fall back to `unknown` section.
3. **Single OCR engine:** only Tesseract is wired. EasyOCR or a vision-LLM
   fallback for low-confidence pages is not implemented.
4. **No multi-period support beyond 2 columns:** `detect_year_column` handles
   up to 2 year columns. 5-year summary tables will produce incorrect column
   alignment.
5. **PDF-native text not used:** all pages are rasterized and OCR'd regardless
   of whether the PDF contains selectable text. Significant speed improvement
   possible by using `pdfplumber` for text-native pages.
