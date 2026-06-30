# HANDOFF — OCR Pipeline (Generalization Complete)
**Branch:** V3  **HEAD:** e59fc8b2723b431b70361d812f6642aa8afc7bfc
**Date:** 2026-06-30
**Status:** Pipeline stable and generalized. Ready for submission or V4.

## F1 Scores (current)
S1: 100% | S2: 94.44% | S3: 100% | Overall: 98.11%

## What the pipeline does
The pipeline ingests PDF documents, renders them to images, and extracts structural text tokens via Tesseract (or PaddleOCR). It clusters these tokens by spatial coordinates into rows and dynamic columns. It then applies fuzzy matching via rapidfuzz to align document descriptions against config-defined field criteria. Finally, a validation layer enforces structural accounting constraints (e.g., Assets = Liabilities + Equity) through an exhaustive permutation repair engine.

## Architecture decisions made (and why)
- DPI-aware scaling: all pixel thresholds scale with render_dpi/300
- Confidence-based fallback: cell selection by Tesseract token confidence (attempted, reverted due to F1 regression)
- Config-driven sign convention: sign: negative in field_config.yaml
- X-coordinate year column detection: not index-based, not fragile to note refs
- Repair engine: exhaustive permutation search before inferred_implicit_sum

## Known limitations (honest)
- S2 one miss (14,095,953 read as 11,095,953): OCR char confusion, unrecoverable
- Keywords cover AU/SGD financial statements; other jurisdictions need additions
- Repair engine equations assume standard P&L + BS structure; different layouts
  (e.g. IFRS vs local GAAP variations) may require new equations
- No page classifier: pipeline processes all pages equally (no income statement
  vs balance sheet awareness)
- Token confidence fallback causes F1 regression on Paid Up Capital due to higher OCR confidence on smaller numbers (e.g., '10' vs '72,338').

## How to run
python tools/generate_evaluation_report.py --optimize

## What V4 should tackle (if needed)
- Page classifier (spatial, not substring-based)
- Expand keyword list for new jurisdictions
- LLM-assisted field disambiguation for edge cases
