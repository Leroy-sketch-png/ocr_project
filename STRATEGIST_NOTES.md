# CRITICAL ARCHITECTURE FAILURES — V3 POST-MORTEM
## For the Remote Strategist Team

**Branch:** `V3`  
**Author:** Pipeline Agent  
**Date:** 2026-06-29  
**Status:** 🔴 NOT PRODUCTION READY — Read every word of this document before touching the code.

---

## LATEST UPDATE: WHY YEAR-COLUMN INDEX DETECTION FAILS

We attempted to fix year-column extraction by detecting the year header row (e.g., `['2025', '2024']`) and using the most-recent year's cell index. **This made things worse (F1 75.47% vs 91.43%).**

The root cause: **note-reference columns shift cell indices between rows on the same page.**

```
Year header row:       cell[0]="2025"   cell[1]="2024"    → index 0 = most recent year
Revenue row:           cell[0]="4"      cell[1]="40,580"  cell[2]="101,563" → note ref at 0 SHIFTS data
Cash row:              cell[0]="18,742" cell[1]="13,916"  → NO note ref, data at index 0
```

Index 0 means "2025" in the year header. But in data rows, index 0 could be a note reference,
the 2025 value, OR the 2024 value depending on whether the row has a note reference.

**You cannot solve this with column indices. Full stop.**

### The Only Correct Fix: X-Position Based Column Alignment

The OCR tokens have bounding box pixel coordinates. A `TableRow` already stores `cell_tokens: List[List[Token]]` — the bounding boxes are available. The fix is:

1. When a year header row is detected, record the **pixel X-center** of each year cell.
2. For every data row on the same page, match each cell to the nearest year column by X-position, not by array index.
3. This is immune to note-reference column shifting.

**This requires modifying `build_table_rows` to preserve spatial context and expose an `x_position_select(target_x: float)` method on `TableRow`.**

This is a `table_builder.py` rewrite — estimated 2–4 hours of focused work for someone who understands the OCR token model. It is the single highest-ROI engineering investment available.

---



---

## THE LIE WE HAVE BEEN TELLING OURSELVES

Our pipeline passes Sample 1 at **100% F1** and Sample 2 at **94.44% F1**. These numbers are misleading. The pipeline is not extracting the right year's column by design — it is doing so **by accident** for those two samples. Sample 3 breaks because it exposes the underlying architectural void.

### Why Sample 1 and 2 "work" by accident

Every financial statement table has this structure:

```
                        2025        2024
Revenue                40,580     101,563
Cost of Sales         (16,885)    (86,254)
```

The current `field_extractor.py` logic (line 188) skips any `idx == 0` cell if it is a short integer ≤ 2 digits (note references like `'4'`, `'8'`, `'10'`). It then blindly takes the **next cell** as the value. In Sample 1 and Sample 2, the note reference is always in cell[0], so cell[1] happens to be the most recent year. In Sample 3, the note reference is sometimes absent, so cell[0] IS the most recent year — but sometimes it's the previous year.

**The pipeline has no year-awareness whatsoever.** It has never had it.

---

## CONFIRMED EVIDENCE FROM DUMP FILES

### Sample 3 — Balance Sheet (Page 7)

```
PAGE 7 ROW: Note fe | CELLS: ['2023', '2022']
PAGE 7 ROW: Cash and cash equivalents | CELLS: ['8', '972,064']
PAGE 7 ROW:  | CELLS: ['1,444,515', '1,085,782']
PAGE 7 ROW: Total assets | CELLS: ['1,450,111', '1,086,669']
PAGE 7 ROW: Trade and other receivables | CELLS: ['4', '113,718']
```

Column structure:
- Cell[0] = Note reference OR 2023 value
- Cell[1] = 2022 value (prior year)

**Ground truth expects 2023 (most recent) values.**

The pipeline extracted `113,718` for Trade Receivables — this is the **2022** value. The correct **2023** value is `74,677`. The pipeline is picking the wrong year and our benchmark says it's wrong. This is the **correct** failure — our evaluation IS catching it.

### Sample 2 — Income Statement (Page 2)

```
PAGE 2 ROW: Note | CELLS: ['2024', '2023']
PAGE 2 ROW: Revenue | CELLS: ['97 852 971', '83 876 837']
PAGE 2 ROW: Annual result =: | CELLS: ['9 915 794', '11 646 955']
```

The pipeline grabs cell[0] = `97,852,971` (2024, correct). This works **by accident** because there is no note reference in cell[0] for this sample. However, Net Profit (`Annual result`) is also grabbed from cell[0] correctly (`9,915,794`). 

**But then why does Net Profit fail?**

The repair engine runs after extraction. The repair engine found a combinatorial assignment where swapping Annual result to `0` satisfied some other equation constraint. This is the repair engine destroying correct data trying to fix phantom inconsistencies.

### Sample 1 — Balance Sheet (Page 7)

```
PAGE 7 ROW: Note | CELLS: ['2025', '2024']
PAGE 7 ROW: Cash and cash equivalents | CELLS: ['18,742', '13,916']
PAGE 7 ROW: Trade and other receivables | CELLS: ['20,444', '26,605']
```

No note references in cell[0] here. The pipeline grabs cell[0] = most recent year. **Works by accident.**

---

## THE FOUR ROOT FAILURES (In Order of Severity)

### FAILURE 1: No Year Column Detection (CRITICAL)

**Severity:** Pipeline-destroying on any document with multi-year tables.  
**Description:** The pipeline has zero mechanism to identify which column corresponds to the reporting period. It guesses based on skip-short-integer heuristics. This is not engineering — it is gambling.

**Required Fix:**
1. Detect the year header row: scan for a row whose cells are 4-digit integers in the range 1990–2030 (e.g., `['2024', '2023']`).
2. Record the **column index** of the maximum year (most recent = target).
3. Pass this column index into `field_extractor.py` so every row extracts from the correct column.
4. If no year header is found, fall back to current behavior (column[0] after skipping note references).

This is **not** a nice-to-have. This is existential.

### FAILURE 2: Repair Engine Destroys Correct Extractions (CRITICAL)

**Severity:** Actively makes correct fields wrong.  
**Description:** The repair engine applies equations like `Total Assets = Current Assets + Non-Current Assets`. If any one field is wrong, it can cascade and overwrite the other two with garbage values pulled from OCR candidates. In Sample 3, a wrong `Current Assets` extraction triggered the engine to rewrite `Trade Receivables`, `Cash and Cash Equivalents`, and `Non-Current Assets` — all of which were correct before the engine ran.

**Required Fix:**
The repair engine must have a **trust threshold**. If the proposed repair requires changing a field by more than X% of its original value, it must **reject the repair** and instead mark the equation as unresolvable. Destroying 3 correct fields to fix 1 bad field is a net loss. The formula:

```python
MAX_REPAIR_DELTA_RATIO = 0.05  # 5%
if abs(proposed_val - current_val) / (abs(current_val) + 1e-9) > MAX_REPAIR_DELTA_RATIO:
    skip this repair — it is too destructive
```

### FAILURE 3: Note Reference Cell Skipping Is Brittle (HIGH)

**Severity:** Wrong year extracted on docs where note refs are absent.  
**Description:** The current skip logic (`if idx == 0 and clean_text.isdigit() and len(clean_text) <= 2`) only skips short integers. This misses:
- Note refs that are longer: `'109'`, `'164'`
- Note refs with decimals: `'2.11'`, `'2.14'`
- Rows where the first cell IS the correct value (e.g., Sample 3's blank rows)

**Required Fix:** Year column detection (Failure 1 fix) entirely replaces this hack.

### FAILURE 4: Ground Truth Had an Incorrect Value (MEDIUM)

**Severity:** Corrupts evaluation metrics.  
**Description:** `sample3_gt.json` had `Non-Current Assets = 887` when the correct value per the balance sheet is `5,596` (Right-of-use `886` + Plant & Equipment `1` + Deferred Tax `4,709`). This was corrected in V3 but it reveals that the original labeling pass was done without verifying against the actual document rows.

**Required Fix:** Full re-labeling audit of all 3 samples, with each GT value explicitly cross-referenced to the dump file row and the correct year column. Document must include: `field → page → row description → cell index → raw cell value → parsed value`.

---

## WHAT SAMPLE 3 IS ACTUALLY TEACHING US

Sample 3's balance sheet has note references embedded as the first cell:

```
Cash and cash equivalents | CELLS: ['8', '972,064']   ← cell[0]=note ref, cell[1]=2022 value
                          (missing 2023 value → implicit 1,369,838)
```

The 2023 value for Cash is `1,369,838` but it does not appear on the balance sheet row at all — it appears in the cash flow statement. The balance sheet only shows the closing comparative (`972,064`). This means:

1. The 2023 value is implicit (computable from the cash flow statement: `972,064 + 397,850 = 1,369,838`)
2. Our pipeline cannot extract implicit values from cross-statement reconciliation
3. This is a known limitation that must be documented in ground truth as the rationale

**Until the pipeline supports cross-statement inference, Sample 3's Cash and Cash Equivalents GT should be set to `1,369,838` only if the pipeline is expected to pull from the cash flow statement — otherwise the evaluation is testing the wrong thing.**

---

## GROUND TRUTH AUDIT RESULTS

### Sample 1 (FY2025)
| Field | GT Value | Source Row | Column | Verified |
|---|---|---|---|---|
| Revenue | 40,580 | Page 6: Revenue | cell[1] (2025) | ✅ |
| Cost of Sales | -16,885 | Page 6: Cost of goods sold | cell[0] (2025) | ✅ |
| Gross Profit/Loss | 23,695 | Page 6: Gross profit | cell[0] (2025) | ✅ |
| Net Profit/Loss | -87,557 | Page 6: total comprehensive loss | cell[0] (2025) | ✅ |
| Cash & Cash Equiv | 18,742 | Page 7: Cash and cash equivalents | cell[0] (2025) | ✅ |
| Trade Receivables | 20,444 | Page 7: Trade and other receivables | cell[0] (2025) | ✅ |
| Current Assets | 39,186 | Page 7: blank row (subtotal) | cell[0] (2025) | ✅ |
| Total Assets | 39,186 | Page 7: Total assets | cell[0] (2025) | ✅ |
| Current Liabilities | 27,276 | Page 7: Trade and other payables | cell[1] (2025) | ✅ |
| Total Liabilities | 27,276 | Page 7: Total liabilities | cell[0] (2025) | ✅ |
| Paid Up Capital | 350,000 | Page 7: Share capital | cell[1] (2025) | ✅ |
| Retained Earnings | -374,090 | Page 7: Accumulated losses | cell[0] (2025) | ✅ |
| Total Equity | 11,910 | Page 7: Equity attributable | cell[0] (2025) | ✅ |
| Non-Current Assets | 0.0 | No non-current assets section exists | N/A | ✅ |
| Non-Current Liabilities | 0.0 | No non-current liabilities section exists | N/A | ✅ |

### Sample 2 (FY2024)
| Field | GT Value | Source Row | Column | Verified |
|---|---|---|---|---|
| Revenue | 97,852,971 | Page 2: Revenue | cell[0] (2024) | ✅ |
| Cost of Sales | -93,950,313 | Page 2: Total operating expenses | cell[0] (2024) | ✅ |
| Operating Profit/Loss | 3,951,214 | Page 2: Result of operations | cell[0] (2024) | ✅ |
| Profit/Loss Before Tax | 11,095,953 | Page 2: Operating result before tax | cell[0] (2024) | ✅ |
| Net Profit/Loss | 9,915,794 | Page 2: Annual result | cell[0] (2024) | ✅ |
| Cash & Cash Equiv | 10,699,410 | Page 3: Bank deposits, cash in hand | cell[0] (2024) | ✅ |
| Trade Receivables | 442,531 | Page 3: Accounts receivable | cell[0] (2024) | ✅ |
| Current Assets | 74,778,518 | Page 3: Total current assets | cell[0] (2024) | ✅ |
| Plant & Equipment | 47,769 | Page 3: Fixtures and fittings | cell[0] (2024) | ✅ |
| Non-Current Assets | 58,619 | Page 3: Total fixed assets | cell[0] (2024) | ✅ |
| Total Assets | 74,837,137 | Page 3: TOTAL ASSETS | cell[0] (2024) | ✅ |
| Current Liabilities | 24,864,056 | Page 4: Total current liabilities | cell[0] (2024) | ✅ |
| Non-Current Liabilities | 0.0 | No non-current liabilities | N/A | ✅ |

### Sample 3 (FY2023)
| Field | GT Value | Source Row | Column | Correct? |
|---|---|---|---|---|
| Revenue | 1,782,538 | Page 38: Revenue | cell[0] (2023) | ✅ |
| Cash & Cash Equiv | 1,369,838 | **Cash Flow Statement** Page 10: at May 31 | cell[2] (2023) | ⚠️ NOT on balance sheet |
| Trade Receivables | 74,677 | Page 24: Trade receivables | cell[0] (2023) | ✅ but pipeline extracts 113,718 (2022 value) |
| Current Assets | 1,444,515 | Page 7: blank subtotal row | cell[0] (2023) | ✅ |
| Plant & Equipment | 1.0 | Implied from Non-Current breakdown | N/A | ⚠️ Ambiguous |
| Non-Current Assets | 5,596 | Total Assets - Current Assets | Computed | ✅ (implicit) |
| Current Liabilities | 818,988 | Page 7: blank subtotal row | cell[0] (2023) | ✅ but pipeline extracts 251,676 (Trade payables only) |
| Non-Current Liabilities | 1,463 | Page 7: Borrowings | cell[1] (2023) | ✅ but pipeline extracts 779 |

---

## V5 ARCHITECTURE REQUIREMENTS

### Priority 1 — Year Column Detection (MUST HAVE)

```python
def detect_year_column(table_rows: List[TableRow], target_year: int = None) -> int:
    """
    Scan all table rows for a year-header row (cells containing 4-digit years).
    Returns the cell index corresponding to the most recent year (or target_year).
    Returns 0 if no year header found (safe default).
    """
    for row in table_rows:
        year_cells = []
        for idx, cell in enumerate(row.cells):
            clean = cell.replace(',', '').replace(' ', '').strip()
            if clean.isdigit() and 1990 <= int(clean) <= 2030:
                year_cells.append((idx, int(clean)))
        if len(year_cells) >= 2:
            # Found a year header row — return index of most recent year
            return max(year_cells, key=lambda x: x[1])[0]
    return 0  # fallback: take first cell
```

This year column index is then passed into `extract_fields` and used to select the value cell instead of the current skip-short-integer hack.

### Priority 2 — Repair Engine Trust Threshold (MUST HAVE)

```python
MAX_REPAIR_DELTA_RATIO = 0.05

def _is_safe_repair(current_val: float, proposed_val: float) -> bool:
    if current_val is None or current_val == 0:
        return True
    delta_ratio = abs(proposed_val - current_val) / abs(current_val)
    return delta_ratio <= MAX_REPAIR_DELTA_RATIO
```

The engine must call `_is_safe_repair` before overwriting any field. If the repair is unsafe, mark the equation residual as unresolvable and move on — do not cascade damage.

### Priority 3 — Page Classifier (ARCHITECTURE CHANGE NEEDED)

The substring-matching Page Classifier in the V4 plan is **proven to fail**. When tested it dropped overall F1 from 91.43% to 82.0% by misclassifying balance sheet pages in Sample 1 and Sample 3.

The correct approach for V5:
- **Do NOT use substring matching on full page text**
- Instead: locate the year header row (Priority 1), then extract only rows that are within ±N rows of a year-header row on the same page
- This is spatial, not semantic — it correctly handles any document structure

### Priority 4 — Cross-Statement Inference (FUTURE)

Some values (like Sample 3's Cash) are only available in the Cash Flow Statement but are expected on the Balance Sheet. This requires cross-statement reconciliation — out of scope for V5 but must be documented.

---

## EVALUATION SYSTEM REFORM

The current evaluation system compares extracted values against a hand-labeled JSON with a simple equality check. This has the following problems:

1. **No tolerance for rounding**: `9,915,794` vs `9915794.0` — passes. But `74,677` vs `74677.0` — fails if one is a float parse artifact.
2. **No column annotation**: GT values are not annotated with which year/column they came from. When the pipeline extracts the wrong year, the evaluation correctly flags it, but we cannot tell from the report whether the correct year was even attempted.
3. **No reason logging in report**: The evaluation report shows `extracted: 113,718` but not `reason: "highest scoring row on page 7"`. Without this, debugging is impossible.

**Required additions to the evaluation report:**
```json
{
  "field": "Trade Receivables",
  "expected": 74677.0,
  "extracted": 113718.0,
  "raw_text": "113,718",
  "reason": "highest_kw_score_row",
  "page": 7,
  "row_description": "Trade and other receivables",
  "cell_index": 1,
  "all_cells": ["4", "113,718"]
}
```

This gives the strategist everything needed to debug a single run without touching the code.

---

## WHAT IS IN THE CURRENT V3 BRANCH

| Commit | What Changed | Effect |
|---|---|---|
| `d64d6be` | V3 Steps 1-7 core | +6% F1 overall, Sample 1 → 100% |
| `dc17011` | Implicit sum fallback in repair engine, note filter tightening | Repair engine safer, filters section-header decimals |

**Current metrics (V3):** Precision 92.31% / Recall 90.57% / F1 91.43%  
**Honest assessment:** Sample 1 is correctly at 100%. Sample 2 at 94.44% is one field off due to a repair engine cascade. Sample 3 at 80% is structurally broken due to year-column blindness.

---

## WHAT THE STRATEGIST MUST DECIDE BEFORE V5

1. **What is the target year?** Should the pipeline always extract the most recent year? Or should it be configurable per document (e.g., `target_year=2023`)? This affects the year column detection algorithm.
2. **What is the tolerance for repair engine changes?** 5% delta? 10%? Or should we simply not repair any field that was already extracted with a score above 90?
3. **Should Gross Profit/Loss be computed, not extracted?** For both Sample 1 and Sample 2, the document does not have a "Gross Profit" total row — it is implied by Revenue + Cost of Sales. The repair engine should handle this, not the extractor.
4. **Is cross-statement inference in scope for V5?** Cash and Cash Equivalents in Sample 3 only exists on the Cash Flow Statement, not the Balance Sheet. Do we expect the pipeline to cross-reference statements?
