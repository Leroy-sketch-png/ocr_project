# Evaluation Report

Generated: 2026-06-29T14:47:02

## Overall
- Expected fields: 53
- Extracted fields: 53
- Correct fields: 46
- Precision: 86.79%
- Recall: 86.79%
- F1: 86.79%

## sample1
- PDF: `AA_SAMPLE1.pdf`
- Precision: 100.00%
- Recall: 100.00%
- F1: 100.00%
- Expected fields: 17
- Extracted fields: 17
- Correct fields: 17
- Error: Review the following fields: Operating Profit/Loss, Plant and Equipment

## sample2
- PDF: `AA_SAMPLE2.pdf`
- Precision: 94.44%
- Recall: 94.44%
- F1: 94.44%
- Expected fields: 18
- Extracted fields: 18
- Correct fields: 17
- Mismatches:
  - Net Profit/Loss: expected `9915794.0` got `0.0`

## sample3
- PDF: `AA_SAMPLE3.pdf`
- Precision: 66.67%
- Recall: 66.67%
- F1: 66.67%
- Expected fields: 18
- Extracted fields: 18
- Correct fields: 12
- Error: Review the following fields: Operating Profit/Loss
- Mismatches:
  - Trade Receivables: expected `74677.0` got `113718.0`
  - Current Assets: expected `1444515.0` got `1483556.0`
  - Plant and Equipment: expected `1.0` got `147.0`
  - Non-Current Assets: expected `5596.0` got `-33445.0`
  - Current Liabilities: expected `818988.0` got `251676.0`
  - Non-Current Liabilities: expected `1463.0` got `779.0`

## Notes
- This report is generated from the bundled ground-truth files.
- The OCR output artifact is written separately for direct review.
