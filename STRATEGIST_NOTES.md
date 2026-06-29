# Strategist Briefing: Round 2 (The Path to 100%)

## Current State
The pipeline is fundamentally stable. We purged the "bluffing" from the ground truth and standardized exactly 19 fields across all samples. The metric tracking is now mathematically sound and strictly penalizes hallucination. 

**Official Validated Metrics:**
- Precision: 95.74%
- Recall: 84.91%
- F1: 90.0%

## What We Learned from V2
1. **Financial Stopwords:** Stripping words like "net", "total", and "other" from the labels destroys semantic meaning in accounting. We restored them to the stopword blocklist in `compute_match_score` so the engine knows the difference between "Gross Profit" and "Net Profit".
2. **String Cleaning in Fuzz Fallback:** Leaving punctuation in the final string fallback caused `fuzz.ratio` to falsely score "Annual result" higher than "Annual result =:" (which had the correct number). We now force the fallback to use `q_clean` and `d_clean` so punctuation doesn't break tie-breaker logic.
3. **Keyword Danger ("fixed assets"):** Using highly generic terms like "fixed assets" for Plant and Equipment is toxic. It causes the engine to blindly anchor onto Deferred Tax calculation tables instead of the balance sheet. We removed it and restored exact terms like "Fixtures and fittings".

## The 5 Remaining Hard Failures (Plus Ultra Targets)
We have included the raw OCR dumps for all samples in `artifacts/dumps/`. Use them to design the next structural leap for the engine.

1. **"Trade and other payables" mapping to Current Liabilities (Sample 1)**
   - The engine correctly identifies the value (27,276), but it's not extracting it because the keywords for Current Liabilities don't include "Trade and other payables" or the engine isn't properly classifying it as a liability sub-total.
2. **Non-Current Liabilities Anchor Collision (Sample 2)**
   - Sample 2 has NO non-current liabilities. The engine is incorrectly extracting the Current Liabilities value (24,864,056) for the Non-Current Liabilities field.
3. **Auditor's Opinion Norwegian Format (Sample 2)**
   - The unqualified language is buried in body text ("In our opinion, the accompanying financial statements present fairly...") rather than a neat heading. The engine fails to anchor to the paragraph.
4. **Plant & Equipment Note Number Anchor (Sample 3)**
   - Expected = 1.0. Extracted = 3.0. The engine is locking onto the note reference "3." on page 24 instead of the actual carrying value "1" on the same page.
5. **Non-Current Assets Aggregation (Sample 3)**
   - Expected = 887. The document does not have a "Total Non-Current Assets" header. It only lists the line items: Right-of-use assets (886) + Plant and equipment (1). The engine returns `null` because the heading doesn't exist. We may need an aggregation engine or structural inference.

## The Objective
We need targeted architectural additions to the extraction algorithm to conquer these 5 specific edge cases. Use the dumps to formulate the next plan.
