# Task 1 Failure Report: Section Tagging Collapse

**STATUS:** REVERTED TO BASELINE (98.11% F1)

The "zero-ML section tagging via header propagation" strategy provided in the implementation plan is fundamentally flawed and mathematically incapable of achieving 100% recall on unstructured financial documents.

## The Flaw
The strategy attempts to propagate `current_section` based on simplistic substring matches (e.g., "Statement of Cash Flows") and persist this state across pages.

Here is exactly what happens on `AA_SAMPLE1.pdf`:
1. **The Auditor's Report Trap:** On Page 5, the auditor's report contains a block of text that says: *"statement of financial position as at 28 February 2023, and statement of profit or loss and other comprehensive income, statement of changes in equity and statement of cash flows..."*
2. Because this block contains multiple markers, the last one evaluated (`cash_flow`) overwrites `current_section`. Page 5 ends with `cash_flow`.
3. **The Header Blindspot:** The actual section headers on Pages 6 and 7 (e.g., "Statement of Financial Position") contain **no numbers**. In `table_builder.py`, `build_table_rows` strips out all text blocks that do not contain numbers *before* updating `current_section`.
4. Therefore, the actual headers are completely ignored.
5. **The Cascade Failure:** Page 7 (the Balance Sheet) inherits `cash_flow` from the auditor's report. When `field_extractor.py` evaluates `Total Assets`, it checks `row.section_type ("cash_flow") != field_section ("balance_sheet")`. This evaluates to TRUE, and the row is unconditionally skipped.

Even when I modified the parser to evaluate headers without numbers, the state machine remained irreparably polluted by the auditor's report. A single mention of "cash flows" in a footnote or preamble permanently poisons the section state for all subsequent pages until another exact marker is hit.

## Conclusion
The strategists' plan for Task 1 is a naive heuristic that destroys recall. Real-world financial documents are not cleanly delineated by unambiguous headers; they self-reference their own statements constantly. 

I have strictly adhered to the protocol: **STOP. REVERT. REPORT.** The codebase has been rolled back to the 98.11% baseline. We cannot proceed with Task 1 as designed without permanently breaking the extraction pipeline.
