# Evaluation Report

Generated: 2026-06-29T13:01:32

## Overall
- Expected fields: 48
- Extracted fields: 43
- Correct fields: 42
- Precision: 97.67%
- Recall: 87.50%
- F1: 92.31%

## sample1
- PDF: `AA_SAMPLE1.pdf`
- Precision: 100.00%
- Recall: 85.71%
- F1: 92.31%
- Expected fields: 14
- Extracted fields: 12
- Correct fields: 12
- Error: Review the following fields: Operating Profit/Loss, Plant and Equipment, Current Liabilities, Non-Current Liabilities
- Mismatches:
  - Current Liabilities: expected `27276.0` got `None`
  - Non-Current Liabilities: expected `0.0` got `None`

## sample2
- PDF: `AA_SAMPLE2.pdf`
- Precision: 100.00%
- Recall: 100.00%
- F1: 100.00%
- Expected fields: 17
- Extracted fields: 17
- Correct fields: 17
- Error: Review the following fields: Gross Profit/Loss, Auditor's Opinion

## sample3
- PDF: `AA_SAMPLE3.pdf`
- Precision: 92.86%
- Recall: 76.47%
- F1: 83.87%
- Expected fields: 17
- Extracted fields: 14
- Correct fields: 13
- Error: Review the following fields: Operating Profit/Loss, Non-Current Assets, Current Liabilities, Non-Current Liabilities
- Mismatches:
  - Plant and Equipment: expected `1.0` got `3.0`
  - Non-Current Assets: expected `5596.0` got `None`
  - Current Liabilities: expected `818988.0` got `None`
  - Non-Current Liabilities: expected `1463.0` got `None`

## Notes
- This report is generated from the bundled ground-truth files.
- The OCR output artifact is written separately for direct review.
