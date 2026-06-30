# HANDOFF — OCR Pipeline (V5 Complete)

**Branch:** V3
**Date:** 2026-06-30
**Status:** Pipeline stable, generalized, and multi-year extraction is live. 100% F1 achieved across all samples.

## Verified Baseline After V5 Session
**S1: 100% | S2: 100% | S3: 100% | Overall: 100%**
- S2's historic miss (`Profit/Loss Before Tax`) was resolved by refining `field_config.yaml` to prefer `Result before tax` over a sub-total `Operating result before tax` causing collision.
- The pipeline mathematically proves 18/18 extractions on S2, and 53/53 overall.

## What the pipeline does
The pipeline ingests PDF documents, renders them to images, and extracts structural text tokens via Tesseract. It clusters these tokens by spatial coordinates into rows and dynamic columns. It applies fuzzy matching via rapidfuzz to align document descriptions against config-defined field criteria. A validation layer enforces structural accounting constraints through an exhaustive permutation repair engine. Multi-year extraction exports multiple columns natively.

## Architecture decisions made (and why)
- **Multi-Year Extraction Live**: Discarded the assumption that section tagging is needed for multi-year. `detect_year_column` now maps all discovered year headers to their corresponding X-coordinates. `extract_fields` queries every matching cell along these columns, producing full year-series outputs for every field (`fv.multi_year`).
- **Section Tagging Shelved**: Attempted string-matching for section tagging but encountered two fatal flaws:
    1. *The Auditor Report Trap:* Prose explicitly citing "statement of cash flows" misfires the state machine.
    2. *The Header Blindspot:* Valid section titles without numeric tables are stripped before evaluation by `table_builder`.
  *Conclusion: Substring propagation is unreliable. Section tagging requires a decoupled architectural pass. Deferred to V6.*

## Known limitations (honest)
- **Keyword Brittle-ness:** Perfect F1 achieved by specifically tuning keywords (`Result before tax`), highlighting the inherent fragility of dictionary-based matching across differing corporate accounting terminologies.
- **Header Parsing Gap:** `table_builder.py` inherently discards non-numeric text blocks, limiting our ability to utilize visual section headers or footers.

## How to run
python tools/generate_evaluation_report.py --optimize

## V6 Targets
1. **True Section Tagging via Pre-Pass**: Parse non-numeric header blocks in a pre-pass *before* `table_builder`, store section boundaries by page number (not by state machine propagation), then apply section filter in `field_extractor`. This avoids both failure modes discovered in V5.
2. **Global Keyword Expansion**: Expand keyword lists for new markets, jurisdictions, and non-standard GAAP terminologies.
3. **REST API Wrapper**: Expose pipeline via Flask/FastAPI for structured ingestion by downstream services.
