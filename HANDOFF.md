# HANDOFF — OCR Pipeline (Generalization Complete)
**Branch:** V3  **HEAD:** e59fc8b2723b431b70361d812f6642aa8afc7bfc
**Date:** 2026-06-30
**Status:** Pipeline stable and generalized. V4 implementation attempted but halted due to evaluation regression.

## F1 Scores (current)
S1: 100% | S2: 94.44% | S3: 100% | Overall: 98.11%

## What the pipeline does
The pipeline ingests PDF documents, renders them to images, and extracts structural text tokens via Tesseract. It clusters these tokens by spatial coordinates into rows and dynamic columns. It applies fuzzy matching via rapidfuzz to align document descriptions against config-defined field criteria. A validation layer enforces structural accounting constraints through an exhaustive permutation repair engine.

## Architecture decisions made (and why)
- **Backward-fill Year X-coordinate**: Cover pages with missing headers now use the nearest future page's year column.
- **Argmax Confidence Fallback**: Eliminated the arbitrary `abs(v)>100` hack; extraction falls back to the highest OCR confidence token among cell tokens.
- **Zero-Guard Update**: Primary P&L fields demote zeros to `row_candidate` if a non-zero value is already safely extracted.
- **Repair Engine Awareness**: Refactored `_is_safe_repair` to bypass 15% delta checks for sign flips and column shifts, ensuring safe mathematical recovery.
- **Aborted Section Classifier (V4 Task 4)**: Attempted zero-ML statement-type tagging via header propagation. Reverted immediately because F1 plummeted (98.11% -> 76.92%). The assumption that headers universally precede related line items fails on unstructured inputs (e.g., S1 has no headers, missing all extractions). 
- **Skipped Multi-Year Array (V4 Task 5)**: Skipped as it relies on the section-type infrastructure which was reverted.

## Known limitations (honest)
- S2 one miss (14,095,953 read as 11,095,953): OCR char confusion, mathematically unrecoverable.
- V4 "Section Headers" assumption is brittle for unstructured PDFs missing formal section titles.
- Missing an explicit Page Classifier model to distinguish Income Statements from Balance Sheets.
- No multi-year JSON export yet.

## How to run
python tools/generate_evaluation_report.py --optimize

## What V5 should tackle
- Deep learning page classifier (spatial layout, rather than substring header propagation).
- Implement Multi-year array mapping (`Dict[str, List[FieldValue]]`) decoupled from string header-based section tagging.
- Expand keyword lists.
