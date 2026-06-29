# Deep Implementation Plan V2 — OCR Project: Going Extreme

> **Branch:** `fix/trainer-feedback-round1` → then promote to a new `feature/deep-v2` branch
> **Status of Round 1 fixes:** All 9 tasks from the previous plan are DONE and verified in the branch.
> **Purpose of this document:** Push beyond passing the trainer's checklist. This plan attacks every architectural weakness, every extraction gap, and every robustness hole that exists in the codebase right now. The agent must read the actual source files before touching anything.

---

## Honest Audit — What Is Actually Wrong Right Now

After reading every file in `src/`, here is a brutally honest diagnosis. These are not stylistic suggestions — they are real failure modes.

---

### A. `field_extractor.py` — Logic Gaps

#### A1. `normalize_auditor_opinion` is order-dependent and incomplete

```python
# Current order in normalize_auditor_opinion:
if "disclaimer of opinion" in text_lower: return "Disclaimer"
if "adverse opinion"       in text_lower: return "Adverse"
if "qualified opinion"     in text_lower: return "Qualified"
if "unqualified opinion"   in text_lower: return "Unqualified"
```

**Problem 1 — "Unqualified" is a substring of "Qualified":** If a document says "unqualified opinion", the check for `"qualified opinion"` fires first (because "qualified" is inside "unqualified") and returns the wrong label `"Qualified"`. The checks must go from most specific to least specific.

**Problem 2 — Missing variants:** Real annual reports use:
- "true and fair view" (implies Unqualified)
- "except for" / "except that" (Qualified)
- "do not give a true and fair" (Adverse)
- "unable to obtain sufficient" (Disclaimer)
- "clean opinion" (Unqualified)

**Problem 3 — `normalize_auditor_opinion(kw)` fallback is useless:** If the block text doesn't match, the code falls back to `normalize_auditor_opinion(kw)` where `kw` is a keyword like `"Basis for Opinion"` — which will always return `None`. This makes every keyword-only match produce `valid=False`.

**Fix:**
```python
def normalize_auditor_opinion(text: str) -> Optional[str]:
    t = text.lower()
    # ORDER MATTERS: check most-specific phrases first
    if any(p in t for p in ["disclaimer of opinion", "unable to obtain sufficient"]):
        return "Disclaimer"
    if any(p in t for p in ["adverse opinion", "do not give a true and fair", "does not give a true and fair"]):
        return "Adverse"
    if any(p in t for p in ["except for", "except that", "with the exception of"]):
        return "Qualified"
    if any(p in t for p in ["unqualified opinion", "unmodified opinion", "true and fair view", "clean opinion", "present fairly"]):
        return "Unqualified"
    return None
```

Remove the `normalize_auditor_opinion(kw)` fallback — replace with a context-window expansion (see A2).

#### A2. Auditor Opinion only searches per-block — misses multi-block context

The opinion paragraph and the opinion type word are often in DIFFERENT text blocks (the heading "Independent Auditor's Report" is one block; "In our opinion..." is the next). The current code checks each block in isolation, so if the keyword is in block N but the opinion type word is in block N+1, it returns `None`.

**Fix:** When `normalize_auditor_opinion` returns `None` for a matched block, also check the next 2 blocks in the list. Pass an index and the full `text_blocks` list.

#### A3. `compute_match_score` score of 82 is a magic number with no documentation

The threshold `>= 82` for field matching is arbitrary and untested. A score of 81 might be a perfect match; 82 might be a garbage match. This becomes especially dangerous with the new keywords added in Round 1 (some of which are short phrases that score lower).

**Fix:**
- Move threshold to `field_config.yaml` as a per-field or global `match_threshold` key so it can be tuned without code changes.
- Log the top-scoring candidate for every field at DEBUG level so you can inspect and tune.

#### A4. `best_cell_idx` selection logic is fragile

```python
best_cell_idx = 0
for idx, cell_text in enumerate(row.cells):
    clean_text = cell_text.replace(",","").replace(".","").replace(" ","").strip()
    if idx == 0 and clean_text.isdigit() and len(clean_text) <= 2:
        continue
    best_cell_idx = idx
    break
```

This skips `idx==0` only if it looks like a small integer (row number). But it accepts the **first non-skipped cell** unconditionally. If a row has [row_num, year_label, current_year_value, prior_year_value], the selected cell will be `year_label` (a string like "2024"), not the actual value. The code should prefer cells that `parse_numeric` can actually parse.

**Fix:** Score all cells by `parse_numeric` success and pick the rightmost cell that parses as a valid number (annual report convention: current year is the last column with a value).

#### A5. `row_candidates` appends alternate cells even when they can't be parsed

In the `elif best_kw_score == current_best_score` branch, `row.cells` are appended to `row_candidates` even if `parse_numeric(cell_text)` returns `None`. This pollutes the repair engine's candidate list with noise.

**Fix:** Only append cells where `cval is not None` to `row_cands` — already done in the `should_update` branch but NOT in the `not should_update` fallback branch. Fix the fallback to filter as well.

---

### B. `repair_engine.py` — Logic Gaps

#### B1. `EQUATIONS` list is incomplete and has wrong signs

```python
("Gross Profit/Loss", ["Revenue", "Cost of Sales"]),
```

Cost of Sales is typically **negative** in a P&L. But `compute_equation_residual` just sums all summands with `+`. This means `GP = Revenue + CoS` only works if CoS is already stored as negative (which Tesseract often gets wrong — brackets `(1234)` vs `-1234`).

**Fix:**
- Add a sign convention to EQUATIONS: `("Gross Profit/Loss", [("Revenue", +1), ("Cost of Sales", -1)])`.
- Update `compute_equation_residual` to respect the sign.
- Add missing equations:
  - `Operating Profit = Gross Profit + Other Income - Operating Expenses`
  - `Non-Current Assets = Property Plant Equipment + Intangible Assets + Other Non-Current`
  - `Current Liabilities = Trade Payables + Short-Term Borrowings + Other Current Liabilities`

#### B2. Phase 1 (combinatorial) tries ALL suspects, not just the weakest one

When a residual exists, the engine should prioritize repairing the field with the lowest OCR confidence first. Currently it iterates `suspects` in order and repairs the first one that fixes the equation — which might be the wrong field. A high-confidence Revenue correction that accidentally satisfies the equation is worse than a low-confidence Trade Receivables correction.

**Fix:** Sort `suspects` by ascending `fv.confidence` (or `fv.valid` status) before Phase 1.

#### B3. `generate_candidates` does not handle thousands separators

Real OCR output might produce `"1,234"` or `"1 234"`. If the thousands separator is mis-read as `"l,234"` or `"1.234"`, the current digit-flip candidates won't catch it because the function only flips individual characters in the CONFUSION_SET, not handle separator mutations.

**Fix:** Add separator normalization candidates — try interpreting `.` as thousands separator (European format) and `,` as decimal, and vice versa.

#### B4. Phase 2 (Sniper OCR) bbox padding is missing

`targeted_ocr(img, fv.bbox)` crops the image to exactly the bounding box. But Tesseract performs much better with a few pixels of padding around the crop — tightly-cropped cells often cut off ascenders/descenders and produce garbage.

**Fix:** In `cell_ocr.py`'s `targeted_ocr`, pad the bbox by at least 4px on each side before cropping.

#### B5. `apply_math_repairs` mutates `repaired_fields` in-place during Phase 1 iteration

In Phase 1, when testing a candidate value, the code sets `repaired_fields[suspect].value = cand_val`, computes the residual, then **restores** `repaired_fields[suspect].value = old_val`. This works for single-field mutations. But if the loop breaks and `best_repair` is set, a subsequent pass on a different equation may see a stale `repaired_fields` state. The final `if best_repair:` block at the end correctly commits the repair — but only after the loop exits. If two equations share a suspect and both have residuals, the second equation's Phase 1 might incorrectly test against an already-dirty `repaired_fields` from the first equation's uncommitted test.

**Fix:** The Phase 1 test block should use a local shallow copy of `repaired_fields` rather than mutating in place.

---

### C. `ocr_engine.py` — Quality Gaps

#### C1. Tesseract PSM mode is not exposed

The current engine uses default PSM (Page Segmentation Mode). For financial tables, `--psm 6` (assume a uniform block of text) or `--psm 4` (assume a single column of text) can be significantly more accurate than the default `--psm 3` (fully automatic). The mode should be configurable per document type.

**Fix:** Add a `psm` parameter to `TesseractEngine.__init__` (default 6 for tables) and pass `--psm {psm}` in the config string.

#### C2. Language is hardcoded to English

If the annual report contains any Vietnamese, Thai, or mixed-language text, Tesseract will garble it. Even for English documents, some reports use locale-specific number formatting.

**Fix:** Read language from `runtime_config.py` or environment, default to `"eng"`, allow override.

#### C3. No minimum confidence filter on returned tokens

Tesseract returns tokens with confidence -1 (layout artifacts) and 0-20 (garbage). These tokens pollute `all_tokens`, confuse `compute_match_score`, and add noise to the repair engine's inverse search.

**Fix:** Filter out tokens where `conf < 30` before returning from `recognize_page`. Expose the threshold as a parameter.

---

### D. `image_processor.py` — Preprocessing Weakness

#### D1. Single preprocessing pipeline for all documents

Annual reports vary wildly: clean digital PDFs, low-res scans, photos of printed documents, documents with colored backgrounds, documents with watermarks. A single pipeline (likely: grayscale → threshold → maybe denoise) will be optimal for some and terrible for others.

**Fix:** Implement an adaptive preprocessing selector:
1. Compute mean pixel variance of the grayscale image.
2. If variance is low (clean digital): apply minimal processing (just grayscale + slight sharpen).
3. If variance is medium (light scan): apply Otsu threshold.
4. If variance is high (noisy scan/photo): apply Gaussian blur + adaptive threshold + morphological opening.

#### D2. No deskewing

Scanned documents are often slightly rotated. Even 1-2 degrees of skew causes Tesseract to merge words from adjacent lines, destroying table structure completely.

**Fix:** Add an optional deskew step using Hough line detection or the `deskew` library (pip install deskew — free, 1 dependency). Apply it before thresholding.

#### D3. No DPI normalization

`pdfplumber`'s `page.to_image(resolution=300)` is in `main.py` but the preprocessing code doesn't know the actual DPI of the input. If someone passes a scanned image at 72 DPI, Tesseract accuracy drops sharply. Optimal Tesseract DPI is 300.

**Fix:** In `image_processor.py`, detect image dimensions and upscale to equivalent 300 DPI if the image appears low-resolution (heuristic: width < 1200px for an A4 page).

---

### E. `table_builder.py` — Structural Fragility

#### E1. Row grouping is likely naive

Annual report tables have multi-row labels (e.g., "Property, plant\nand equipment"), sub-headers (e.g., "Current Assets"), totals rows with indentation, and notes columns. If `build_table_rows` groups purely by Y-coordinate proximity, it will merge label rows with value rows or split single rows across two `TableRow` objects.

**Fix:** Read the actual grouping logic, then add:
- Indent detection: rows whose X-start is > threshold pixels right of the section header are sub-rows (don't merge with the header).
- Sub-header detection: rows with no numeric cells are section labels, not data rows — tag them and skip during field matching.
- Multi-line label merging: consecutive rows where only the label cell has text (no numeric cells) should be merged into the preceding row's description.

#### E2. Column alignment detection

The current code likely assigns cells by X-position ranges. But column boundaries shift between pages and between tables. A "Current Year" column on page 3 may be at X=450 while the same column on page 5 is at X=480 due to table scaling.

**Fix:** Per-table column boundary detection: find the X-positions of all numeric tokens in each table and use clustering (simple k-means or just sorted gaps) to determine column boundaries dynamically.

---

### F. `validator.py` — Too Permissive

#### F1. No range validation

A `Total Assets` value of `999,999,999,999` (likely an OCR artefact of a cell border being read as digits) passes validation because it's a valid float. A `Cash and Cash Equivalents` of `-500,000,000` passes too.

**Fix:** Add field-level expected ranges to `field_config.yaml`:
```yaml
Cash and Cash Equivalents:
  keywords: [...]
  min_value: 0
  max_value: 100_000_000_000
```
In `validator.py`, check `min_value` and `max_value` if defined. Mark as `valid=False` with `reason="out_of_range"` if violated.

#### F2. No cross-field consistency check

After repair, if `Total Assets != Total Liabilities + Total Equity` by more than 1%, the result should be flagged as `reconciliation_failed`.

**Fix:** Add a final reconciliation pass in `validator.py` after all individual field validations. Emit a top-level `reconciliation_status` key in the output.

---

### G. `exporter.py` — Output Completeness

#### G1. `tokens` and `bbox` are exported but raw bounding box is not human-readable

The `bbox` is exported as `[x1, y1, x2, y2]` in pixel coordinates with no reference to page dimensions. A reviewer cannot interpret this without knowing the image size.

**Fix:** Add `bbox_normalized` (values 0.0–1.0 relative to page width/height) computed from the page dimensions stored in the document object.

#### G2. No `extraction_method` field

It's impossible to tell from the output whether a value came from direct extraction, column repair, sniper OCR, or inverse search. The `reason` field only captures the repair reason — if no repair was needed, `reason` is `None`.

**Fix:** Add `extraction_method` to `FieldValue`:
- `"direct"` — first-pass extraction, no repair
- `"column_repair"` — Phase 1.5 alternate column
- `"digit_repair"` — Phase 1 digit flip
- `"sniper_ocr"` — Phase 2 targeted re-OCR
- `"inverse_search"` — Phase 3 math-derived

This makes the output self-documenting and lets the trainer verify every value's provenance.

---

### H. Testing — Currently Zero Automated Tests

The `tests/` directory exists but the agent should check if it has any real tests. Based on the round-1 plan, there were no test-related tasks, strongly suggesting the test suite is empty or minimal. This is the single most important long-term gap.

**Fix:** Add at minimum:
1. `tests/test_normalize_auditor_opinion.py` — unit test every opinion type with real-world phrase variants.
2. `tests/test_compute_match_score.py` — verify threshold behavior for known-good and known-bad descriptions.
3. `tests/test_residual_ok.py` — test `_residual_ok` with values like `0.0`, `1e-3`, `1e-2`, `1e-1`, `-0.005`.
4. `tests/test_generate_candidates.py` — verify digit flips produce expected candidates.
5. `tests/test_repair_engine.py` — integration test: construct synthetic `FieldValue` dicts with a known residual, assert the repair engine fixes the correct field.
6. `tests/test_field_extractor.py` — unit test `extract_fields` with mock `TableRow` and `TextBlock` objects.

---

## Implementation Order for the Agent

Work in this exact order. Each item is a discrete, testable unit.

### Phase 1: Critical Bug Fixes (do first — directly affect correctness)

| # | File | Task | Why critical |
|---|---|---|---|
| P1-1 | `field_extractor.py` | Fix `normalize_auditor_opinion` order and phrase list | Wrong opinion type returned for Unqualified |
| P1-2 | `field_extractor.py` | Remove `normalize_auditor_opinion(kw)` fallback; add 2-block lookahead | Keyword-only matches always return `valid=False` |
| P1-3 | `field_extractor.py` | Fix `best_cell_idx` to prefer rightmost parseable numeric cell | Wrong column selected for many fields |
| P1-4 | `repair_engine.py` | Add sign convention to `EQUATIONS` and fix `compute_equation_residual` | GP repair broken for positive CoS values |
| P1-5 | `cell_ocr.py` | Add 4px padding to `targeted_ocr` bbox crop | Sniper OCR crops too tight, produces garbage |

### Phase 2: Robustness Improvements (do second — affect coverage and reliability)

| # | File | Task |
|---|---|---|
| P2-1 | `image_processor.py` | Adaptive preprocessing selector (3 tiers by image variance) |
| P2-2 | `image_processor.py` | Add optional deskew step |
| P2-3 | `image_processor.py` | DPI upscaling for low-res inputs (width < 1200px) |
| P2-4 | `ocr_engine.py` | Configurable PSM mode (default 6), pass via config string |
| P2-5 | `ocr_engine.py` | Filter tokens with confidence < 30 before returning |
| P2-6 | `repair_engine.py` | Sort suspects by ascending confidence before Phase 1 |
| P2-7 | `repair_engine.py` | Use local copy in Phase 1 mutation test (stop in-place mutation) |
| P2-8 | `repair_engine.py` | Add separator normalization to `generate_candidates` |
| P2-9 | `field_extractor.py` | Move match threshold (82) to `field_config.yaml` as `match_threshold` |

### Phase 3: Output & Validation Improvements (do third)

| # | File | Task |
|---|---|---|
| P3-1 | `models.py` | Add `extraction_method: str = "direct"` to `FieldValue` |
| P3-2 | `repair_engine.py` | Set `extraction_method` on every repair path |
| P3-3 | `exporter.py` | Export `extraction_method` and `bbox_normalized` |
| P3-4 | `field_config.yaml` | Add `min_value`/`max_value` to numeric fields |
| P3-5 | `validator.py` | Range validation against config limits |
| P3-6 | `validator.py` | Final reconciliation pass → `reconciliation_status` in output |

### Phase 4: Tests (do last — write after logic is stable)

| # | File | Task |
|---|---|---|
| P4-1 | `tests/test_normalize_auditor_opinion.py` | All opinion type variants including edge cases |
| P4-2 | `tests/test_compute_match_score.py` | Score threshold validation |
| P4-3 | `tests/test_residual_ok.py` | Tolerance edge cases |
| P4-4 | `tests/test_generate_candidates.py` | Digit flip and separator coverage |
| P4-5 | `tests/test_repair_engine.py` | Synthetic integration test with known residual |
| P4-6 | `tests/test_field_extractor.py` | Mock extraction unit tests |

---

## Expected Output Shape After All Phases

```json
{
  "Trade Receivables": {
    "field_label": "Trade and other receivables",
    "value": 12345678,
    "page": 3,
    "bbox": [120, 440, 380, 465],
    "bbox_normalized": [0.094, 0.573, 0.297, 0.605],
    "confidence": 91.2,
    "valid": true,
    "reason": null,
    "extraction_method": "direct",
    "evidence_text": "Trade and other receivables  12,345,678"
  },
  "reconciliation_status": {
    "balance_sheet_balanced": true,
    "residual": 0.0,
    "equations_checked": 6,
    "equations_failed": 0
  }
}
```

---

## Commit Message (use this exactly)

```
feat: deep v2 — opinion fix, adaptive preprocessing, sign-aware repair, extraction provenance, range validation, test suite
```

Push to `fix/trainer-feedback-round1` (or create `feature/deep-v2` from it if the agent prefers a clean branch).

---

## Agent Instructions

1. Read every file listed above **from the repo** before writing any code.
2. Work Phase 1 → Phase 2 → Phase 3 → Phase 4 in order. Do not skip phases.
3. After each task, re-read the modified file to verify the change is correct before moving on.
4. After Phase 4, run the test suite: `python -m pytest tests/ -v`.
5. Commit with the message above.
6. Do not open a new PR — push to the existing branch.
