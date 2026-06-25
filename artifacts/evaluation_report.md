# Evaluation Report

Generated: 2026-06-25T08:03:01

## Overall
- Expected fields: 48
- Extracted fields: 40
- Correct fields: 39
- Precision: 97.50%
- Recall: 81.25%
- F1: 88.64%

## sample1
- PDF: `AA_SAMPLE1.pdf`
- Precision: 100.00%
- Recall: 78.57%
- F1: 88.00%
- Expected fields: 14
- Extracted fields: 11
- Correct fields: 11
- Error: Review the following fields: Net Profit/Loss, Operating Profit/Loss, Plant and Equipment, Current Liabilities, Non-Current Liabilities
- Mismatches:
  - Net Profit/Loss: expected `-87557.0` got `None`
  - Current Liabilities: expected `27276.0` got `None`
  - Non-Current Liabilities: expected `0.0` got `None`

## sample2
- PDF: `AA_SAMPLE2.pdf`
- Precision: 100.00%
- Recall: 94.12%
- F1: 96.97%
- Expected fields: 17
- Extracted fields: 16
- Correct fields: 16
- Error: Review the following fields: Gross Profit/Loss, Retained Earnings, Auditor's Opinion
- Mismatches:
  - Retained Earnings: expected `48008912.0` got `None`

## sample3
- PDF: `AA_SAMPLE3.pdf`
- Precision: 92.31%
- Recall: 70.59%
- F1: 80.00%
- Expected fields: 17
- Extracted fields: 13
- Correct fields: 12
- Error: Review the following fields: Net Profit/Loss, Operating Profit/Loss, Non-Current Assets, Current Liabilities, Non-Current Liabilities, Auditor's Opinion
- Mismatches:
  - Net Profit/Loss: expected `248084.0` got `None`
  - Plant and Equipment: expected `1.0` got `3.0`
  - Non-Current Assets: expected `5596.0` got `None`
  - Current Liabilities: expected `818988.0` got `None`
  - Non-Current Liabilities: expected `1463.0` got `None`

## Notes
- This report is generated from the bundled ground-truth files.
- The OCR output artifact is written separately for direct review.
