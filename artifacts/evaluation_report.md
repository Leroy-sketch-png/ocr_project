# Evaluation Report

Generated: 2026-06-29T13:44:22

## Overall
- Expected fields: 53
- Extracted fields: 47
- Correct fields: 45
- Precision: 95.74%
- Recall: 84.91%
- F1: 90.00%

## sample1
- PDF: `AA_SAMPLE1.pdf`
- Precision: 100.00%
- Recall: 88.24%
- F1: 93.75%
- Expected fields: 17
- Extracted fields: 15
- Correct fields: 15
- Error: Review the following fields: Operating Profit/Loss, Plant and Equipment, Current Liabilities, Non-Current Liabilities
- Mismatches:
  - Current Liabilities: expected `27276.0` got `None`
  - Non-Current Liabilities: expected `0.0` got `None`

## sample2
- PDF: `AA_SAMPLE2.pdf`
- Precision: 94.12%
- Recall: 88.89%
- F1: 91.43%
- Expected fields: 18
- Extracted fields: 17
- Correct fields: 16
- Error: Review the following fields: Gross Profit/Loss, Auditor's Opinion
- Mismatches:
  - Non-Current Liabilities: expected `0.0` got `24864056.0`
  - Auditor's Opinion: expected `Unqualified` got `None`

## sample3
- PDF: `AA_SAMPLE3.pdf`
- Precision: 93.33%
- Recall: 77.78%
- F1: 84.85%
- Expected fields: 18
- Extracted fields: 15
- Correct fields: 14
- Error: Review the following fields: Operating Profit/Loss, Non-Current Assets, Current Liabilities, Non-Current Liabilities
- Mismatches:
  - Plant and Equipment: expected `1.0` got `3.0`
  - Non-Current Assets: expected `887.0` got `None`
  - Current Liabilities: expected `818988.0` got `None`
  - Non-Current Liabilities: expected `1463.0` got `None`

## Notes
- This report is generated from the bundled ground-truth files.
- The OCR output artifact is written separately for direct review.
