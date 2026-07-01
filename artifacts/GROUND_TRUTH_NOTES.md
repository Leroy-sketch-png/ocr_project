# Ground Truth Notes for OCR Pipeline Agent

This file documents the **verified correct values** per sample and the **known mismatches**
between the current pipeline output and reality. Use this as your reference when debugging
or evaluating extraction accuracy.

Full machine-readable ground truth is in `/ground_truth.json`.

---

## AA_SAMPLE1 — Equestz Pte. Ltd. (SGD, FY ended 28 Feb 2025)

### Known Pipeline Issues
| Field | Pipeline Output | Correct GT Value | Root Cause |
|---|---|---|---|
| `Net Profit/Loss` | **MISSING** from output | **-87,557** | Field not extracted at all — flagged in error string |
| `Non-Current Assets` | 0.0 (via `-` dash) | 0.0 ✅ | Correct value, but label mapping may be misleading — company genuinely has zero non-current assets |
| `Operating Profit/Loss` | MISSING | N/A | This company has no distinct operating profit line — do not force-extract |
| `Plant and Equipment` | MISSING | N/A | Company has no PP&E — do not force-extract |

### Verified Correct Extractions
- Revenue: 40,580 ✅
- Gross Profit: 23,695 ✅
- Profit/Loss Before Tax: -87,557 ✅
- Cash and Cash Equivalents: 18,742 ✅
- Trade Receivables: 20,444 ✅
- Total Assets: 39,186 ✅
- Total Liabilities: 27,276 ✅
- Paid Up Capital: 350,000 ✅
- Retained Earnings: -374,090 ✅
- Total Equity: 11,910 ✅
- Auditor's Opinion: Unqualified ✅

---

## AA_SAMPLE2 — Becuriou AS (NOK, FY ended 31 Dec 2024)

### No known pipeline bugs for this sample.
All key fields extracted correctly. Full breakdown in `/ground_truth.json`.

### Verified Correct Extractions
- Revenue: 97,852,971 ✅
- Net Profit/Loss: 9,915,794 ✅
- Total Assets: 74,837,137 ✅
- Total Liabilities: 24,864,056 ✅
- Total Equity: 49,973,081 ✅
- Auditor's Opinion: Unqualified ✅

---

## AA_SAMPLE3 — [Redacted] Pte. Ltd. (USD, FY ended 31 May 2023)

### Known Pipeline Issues
| Field | Pipeline Output | Correct GT Value | Root Cause |
|---|---|---|---|
| `Plant and Equipment` | **5,595** ❌ | **1** | `field_config.yaml` included `"Right-of-use assets"` as a keyword for P&E — wrong. ROU assets = 5,595 is a **separate line item**. P&E = 1 (net book value after depreciation). **FIXED in field_config.yaml v2.** |
| `Auditor's Opinion` | Qualified ✅ | Qualified ✅ | Correct — auditor issued qualified opinion due to inability to confirm contract liabilities of USD 554,679 |

### Verified Correct Extractions
- Revenue: 1,782,538 ✅
- Cost of Sales: -1,463,028 ✅
- Gross Profit: 319,510 ✅
- Profit/Loss Before Tax: 256,484 ✅
- Net Profit/Loss: 248,084 ✅
- Cash and Cash Equivalents: 1,369,838 ✅
- Trade Receivables: 74,677 ✅
- Total Assets: 1,450,111 ✅
- Total Liabilities: 820,451 ✅
- Total Equity: 629,660 ✅
- **Plant and Equipment: 1** ← pipeline was getting this wrong, now fixed
- **Right of Use Assets: 5,595** ← new dedicated field added to config

### Prior Year (FY2022) Notes
- Revenue, Cost of Sales, Gross Profit for FY2022 are **blank/dash** in the document — company had no trading activity
- Finance Costs FY2022: -197
- Other Operating Expenses FY2022: -29,001
- Net Loss FY2022: -39,632

---

## Summary of Config Changes Made

### `src/field_config.yaml` (committed alongside this file)
1. **Removed** `"Right-of-use assets"` and `"Lease assets"` from `Plant and Equipment` keywords
2. **Added** new `Right of Use Assets` field with correct keywords
3. **Removed** `"Total Operating expenses"` and `"Total Operating Staff Depreciation"` from `Cost of Sales` keywords — these are different line items
4. **Removed** bare `"Borrowings"` from `Non-Current Liabilities` keywords — too generic, caused collisions
5. **Added** `"Less: Cost of sales"` to Cost of Sales keywords (matches SAMPLE3 label exactly)
