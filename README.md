# OCR Financial Document Extractor

Extracts structured financial data from scanned or native PDF annual reports
and returns a validated, confidence-scored JSON object. Targets the 19 core
fields that appear in every IFRS/GAAP financial statement.

**Reference suite accuracy: 57 / 57 fields correct across 3 documents.**

---

## Architecture

```
PDF
 └─ io_loader          Load pages, detect DPI
 └─ image_processor    Deskew, denoise, threshold
 └─ ocr_engine         Tesseract → Token stream (text, bbox, page, confidence)
 └─ table_builder      Cluster tokens → TextBlocks → TableRows
                       detect_page_sections → {page: section} map
 └─ field_extractor    Keyword match + column selection → raw FieldValues
 └─ repair_engine      Multi-phase math repair (see below)
 └─ validator          Required-field checks, confidence downgrade
 └─ exporter           Flat JSON output
```

### Repair Engine Phases

| Phase | Name | Description |
|---|---|---|
| 0A | Null-CoS-without-GP | Nulls Cost of Sales when no Gross Profit line exists (services P&L) |
| 0B | Zero-NCL inference | Sets CL = TL when NCL is structurally absent; guarded by keyword score ≥ 80 |
| 1 | Digit mutation | Single-character OCR substitution via CONFUSION_SET |
| 1.5 | Column selection | Tries alternate column values from row_candidates |
| 1.6 | Joint solver | One missing field + one misread digit; derives implied value, scans tokens |
| 2 | Sniper OCR | Re-OCRs the suspicious bounding box |
| 3 | Inverse search | One field missing; scans all tokens + split-merge for closing value |
| 4 | Closure check | Warns if TL ≠ CL + NCL after all repairs |

---

## Quickstart

### Prerequisites

- Python 3.9+
- Tesseract OCR installed and on `PATH`
- Poppler (`pdftoppm`) installed and on `PATH`

### Install

```bash
# Windows
setup.cmd

# PowerShell
.\setup.ps1

# Manual
pip install -r requirements.txt
```

### Run CLI

```bash
# Basic extraction
python -m src.main path/to/report.pdf

# Full repair + optimization mode (recommended)
python -m src.main path/to/report.pdf --optimize

# Write output to file + debug logging
python -m src.main path/to/report.pdf --optimize --output result.json --debug
```

### Run API server

```bash
# Windows
run.cmd

# PowerShell
.\run.ps1
```

POST a PDF to `http://localhost:8000/extract`:

```bash
curl -X POST http://localhost:8000/extract \
  -F "file=@report.pdf"
```

### Run evaluation suite

```bash
python -m tests.evaluate
```

---

## Output Contract

Every field in the JSON output follows this schema:

```json
{
  "Revenue": {
    "value": 383285.0,
    "raw_text": "383,285",
    "page": 3,
    "confidence": "high",
    "reason": "keyword_match",
    "valid": true
  }
}
```

### Confidence Levels

| Level | Meaning | Recommended action |
|---|---|---|
| `high` | Direct keyword match, unambiguous column | Accept automatically |
| `medium` | Repaired via accounting equation or column swap | Soft review |
| `low` | Inferred (zero-NCL) or nulled (no-GP) | Human review required |
| `inferred` | Filled by inverse token search | Human review required |

### When a field cannot be extracted

The field is returned as:

```json
{
  "Revenue": {
    "value": null,
    "valid": false,
    "reason": "not_found"
  }
}
```

The pipeline **never fabricates a value**. A `null` with `valid: false` is
always preferable to a wrong number with `valid: true`.

---

## Extracted Fields

### Income Statement
`Revenue` · `Cost of Sales` · `Gross Profit/Loss` · `Operating Profit/Loss`
· `Profit/Loss Before Tax` · `Net Profit/Loss` · `Income Tax Expense`

### Balance Sheet
`Cash and Cash Equivalents` · `Trade Receivables` · `Current Assets`
· `Non-Current Assets` · `Total Assets` · `Current Liabilities`
· `Non-Current Liabilities` · `Total Liabilities` · `Paid Up Capital`
· `Retained Earnings` · `Total Equity`

### Document-level
`Auditor's Opinion` · `currency` (extracted from column headers)

---

## Project Structure

```
ocr_project/
├── src/
│   ├── main.py             CLI entry point + process_file()
│   ├── api.py              FastAPI server
│   ├── field_config.yaml   Field definitions, keywords, section tags
│   ├── models.py           FieldValue, TableRow, TextBlock, Token
│   ├── io_loader.py        PDF loading + DPI detection
│   ├── image_processor.py  Page preprocessing (deskew, threshold)
│   ├── ocr_engine.py       Tesseract wrapper
│   ├── table_builder.py    Token clustering, section detection
│   ├── field_extractor.py  Keyword matching, year-column detection
│   ├── repair_engine.py    Multi-phase math repair
│   ├── cell_ocr.py         Targeted bbox re-OCR
│   ├── value_parser.py     Numeric string normalisation
│   ├── validator.py        Post-repair field validation
│   └── exporter.py         JSON serialisation
├── tests/
│   ├── evaluate.py         Reference suite runner
│   └── ground_truth.json   57 ground-truth field values
├── CHANGELOG.md            Full engineering history
├── requirements.txt
├── pyproject.toml
├── setup.ps1 / setup.cmd
└── run.ps1 / run.cmd
```

---

## Known Limitations

See [CHANGELOG.md](CHANGELOG.md) — *Known Limitations* section for the
current list of 5 items targeted for V4.

Short version:
- Notes-page token passthrough is intentionally broad (fix in V4)
- Section classifier requires OCR-readable text headers
- Single OCR engine (Tesseract only)
- Maximum 2 year-columns supported
- PDF-native text not used (all pages rasterized)

---

## Running Tests

```bash
# Full reference suite (57 fields, 3 documents)
python -m tests.evaluate

# Single document smoke test
python -m src.main AA_SAMPLE1.pdf --optimize --debug
```

---

*Built on V3 branch · Last updated 2026-07-01*
