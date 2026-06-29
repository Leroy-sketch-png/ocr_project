# Evaluation Report

Generated: 2026-06-29T14:28:26

## Overall
- Expected fields: 53
- Extracted fields: 52
- Correct fields: 48
- Precision: 92.31%
- Recall: 90.57%
- F1: 91.43%

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
- Error: Review the following fields: Gross Profit/Loss
- Mismatches:
  - Net Profit/Loss: expected `9915794.0` got `0.0`

## sample3
- PDF: `AA_SAMPLE3.pdf`
- Precision: 82.35%
- Recall: 77.78%
- F1: 80.00%
- Expected fields: 18
- Extracted fields: 17
- Correct fields: 14
- Error: Review the following fields: Operating Profit/Loss, Non-Current Assets
- Mismatches:
  - Plant and Equipment: expected `1.0` got `147.0`
  - Non-Current Assets: expected `5596.0` got `None`
  - Current Liabilities: expected `818988.0` got `251676.0`
  - Non-Current Liabilities: expected `1463.0` got `779.0`

## Notes
- This report is generated from the bundled ground-truth files.
- The OCR output artifact is written separately for direct review.
