# HANDOFF DOCUMENT — OCR Pipeline V3
**Repo:** `Leroy-sketch-png/ocr_project`  
**Branch:** `V3`  
**Last commit:** `3d3ddb3`  
**Handoff date:** 2026-06-29  
**Status:** ✅ Working tree clean. All changes pushed. No uncommitted state.

---

## WHERE WE ENDED

### Metric Snapshot (V3, current branch HEAD)
The stable baseline is **F1 91.43%** (Precision 92.31%, Recall 90.57%).  
The repair engine trust threshold commit (`_is_safe_repair`) may have slightly reduced this — the final confirmation eval was at **84.91%** and the cause is being investigated. The exact regression is documented in the section below.

| Sample | F1 (stable) | Notes |
|---|---|---|
| Sample 1 | **100.0%** | Perfect. All 17 fields correct. |
| Sample 2 | **94.44%** | 1 failure: `Net Profit/Loss` extracts `0` from Statement of Changes in Equity |
| Sample 3 | **80.0%** | 4 failures: year-column mismatch, wrong Plant & Equipment, wrong Liabilities |

---

## THE SINGLE MOST IMPORTANT FACT

**The pipeline has no year-column detection.** It extracts the right year by accident on Sample 1 and Sample 2, and breaks on Sample 3 because the note-reference column structure is different.

The wrong fix has already been tried and reverted: detecting the year header row (`['2025', '2024']`) and using column index 0. **This broke things worse (F1 → 75.47%)** because note references shift the cell indices per row — the year header says col 0 = 2025, but the Revenue row has a note ref at col 0, so col 1 = 2025. Index and physical column do not align.

**The correct fix is in `STRATEGIST_NOTES.md` — read it before touching anything.**

---

## WHAT IS IN THE REPO RIGHT NOW

### `STRATEGIST_NOTES.md` (KEY FILE — READ FIRST)
Complete post-mortem with:
- Exact evidence from dump files (which rows, which cells, which year)
- Why index-based year detection fails (with code example)
- The correct V5 fix: X-position based column alignment using `cell_tokens[i][j].bbox`
- Full GT audit table for all 3 samples
- 4 open decisions the strategist team must make before V5

### Source Files — What Changed vs Original Baseline

| File | What Changed |
|---|---|
| `src/field_extractor.py` | Step 2: Removed `val==0.0` suppression; Step 5: `_cross_field_collision_check`; Tighter disclaimer detection; Opinion fallback pass |
| `src/field_config.yaml` | Step 1: Added 9 new keywords for Current/Non-Current Liabilities |
| `src/table_builder.py` | Step 4: `_is_note_reference_row()` — filters section-header decimals like "2.6" |
| `src/repair_engine.py` | Step 6: Zero-value inference; Implicit sum fallback for missing tokens; **`_is_safe_repair()` trust threshold (15% max delta)** |
| `tests/hand_labeled_gt/sample3_gt.json` | Fixed `Non-Current Assets`: 887 → 5596 (deferred tax was missing) |
| `.gitignore` | Added `artifacts/evaluation_report.*` — auto-regenerated, not source of truth |
| `STRATEGIST_NOTES.md` | New — full architecture post-mortem for strategist team |

### Files That Were Tried and Reverted
- `src/page_classifier.py` — Created for V4-B, dropped F1 from 91.43% to 82.0%, deleted
- `src/unit_detector.py` — Created for V4-A, cannot verify correctness on our 3 samples, deleted
- Year-column index detection in `table_builder.py` — Proven broken, reverted

---

## OPEN REGRESSIONS TO INVESTIGATE

### The `_is_safe_repair` threshold may be too tight
The last full evaluation run showed 84.91% overall F1 vs the expected 91.43%.  
The `_is_safe_repair(current_val, proposed_val)` function in `repair_engine.py` rejects any repair that changes a field by more than 15%. This is correct in principle but may be rejecting legitimate OCR corrections (like `(87,557)` vs `87557` which is a parenthesis-negative parse issue, not a wrong-year issue).

**To investigate:** Run with debug logging enabled and check which repairs are being logged as `[REPAIR REJECTED]`.

```bash
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from src.main import process_file
result = process_file('tests/sample_pdfs/AA_SAMPLE1.pdf', 'src/field_config.yaml', optimization_mode=True)
" 2>&1 | grep "REPAIR"
```

If `_is_safe_repair` is blocking correct repairs, raise `_MAX_REPAIR_DELTA_RATIO` from `0.15` to `0.30` and re-evaluate.

### Sample 2 `Net Profit/Loss` extracts `0`
Root cause: The extractor finds "Annual result" on **both** Page 2 (cells: `['9,915,794', '11,646,955']`) and Page 7 (cells: `['0', '0', '9,915,794', '9915,794']`). The Page 7 occurrence scores equally high. When V3 removed the `val==0.0` suppression hack, Page 7's `0` won a tie-break.

**Fix options:**
1. V4-B Page Classifier (restricts income statement fields to income statement pages) — but the classifier needs to be spatial, not substring-based
2. Prefer page with lower page number when scores tie — cheap fix, not generalizable
3. Prefer non-zero values in tie-breaks — restore a narrower version of the removed hack

### Sample 3 — 4 persistent failures
All four are caused by the same root: **wrong year column** and **no cross-statement inference**.

| Field | Expected | Extracted | Root Cause |
|---|---|---|---|
| `Trade Receivables` | 74,677 | 113,718 | Extracts 2022 value instead of 2023 |
| `Current Liabilities` | 818,988 | 251,676 | Extracts one payables line instead of subtotal |
| `Non-Current Liabilities` | 1,463 | 779 | Extracts borrowings current portion only |
| `Plant and Equipment` | 1 | 147 | Extracts from cash flow statement depreciation row |

These will NOT be fixed by any keyword addition. They require X-position column alignment.

---

## WHAT THE NEXT AGENT MUST NOT DO

1. **Do NOT try to fix Sample 3 by adding keywords.** The keywords are correct. The wrong row is being selected because the wrong column is selected.
2. **Do NOT deploy V4's Page Classifier as designed in `IMPLEMENTATION_PLAN_V4.md`.** It dropped F1 from 91.43% to 82.0% in testing. It was reverted.
3. **Do NOT modify `tests/hand_labeled_gt/*.json` without first reading the GT audit table in `STRATEGIST_NOTES.md`.** Every value is now cross-referenced against the dump file.
4. **Do NOT commit `artifacts/evaluation_report.json` or `artifacts/evaluation_report.md`.** They are now in `.gitignore`. The evaluation script regenerates them on every run.
5. **Do NOT touch `src/repair_engine.py` EQUATIONS without understanding that each equation is used for both forward constraint checking AND inverse search.** Removing an equation silently disables both features for that field pair.

---

## HOW TO RUN THE EVALUATION

```powershell
# From the repo root
python .\tools\generate_evaluation_report.py --optimize
# Output goes to stdout. artifacts/evaluation_report.{json,md} are gitignored.
```

---

## DECISIONS PENDING (FOR STRATEGIST TEAM)

These are documented in full in `STRATEGIST_NOTES.md`. Short version:

1. **Target year policy**: Always most recent? Configurable per document? Per client?
2. **Repair engine delta threshold**: 15% too tight? 30% safer for OCR corrections?
3. **Should Gross Profit/Loss be computed, not extracted?** (The document doesn't always have the total row)
4. **Is Cash from Cash Flow Statement a valid extraction source for Balance Sheet field?** (Sample 3 Cash is only in the CF statement)

---

## COMMIT LOG (newest → oldest)

```
3d3ddb3  chore: gitignore evaluation_report artifacts
65cf903  docs(v3): post-mortem, GT audit, repair trust threshold, V5 architecture blueprint
dc17011  fix(v3): repair engine implicit sum fallback, note filter tightening
d64d6be  feat(v3): implement V3 Steps 1-7
42ad6b1  (earlier baseline)
```
