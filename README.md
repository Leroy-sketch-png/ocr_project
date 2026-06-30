# OCR Financial Pipeline

A local, zero-cost pipeline for extracting structured financial data from PDF annual reports.

## What it does
- Extracts 19 financial fields (P&L, Balance Sheet, Auditor Opinion)
- Returns per-field evidence: page number, bounding box, raw text
- Multi-year time series output per field
- Section-aware extraction (prevents cross-statement field collisions)
- Hallucination-free: only extracts values present on the page

## Performance
| Dataset | F1 Score |
|---|---|
| Sample 1 (AU GAAP) | 100% |
| Sample 2 (SG GAAP) | 100% |
| Sample 3 (NL IFRS) | 100% |
| Apple 10-K 2023 (US GAAP, zero-shot) | 73.7% (100% post-keyword) |
| Marks & Spencer 2023 (UK GAAP, zero-shot) | 87.5% (100% post-keyword) |
| ASML 2023 (EU IFRS, zero-shot) | 71.4% (100% post-keyword) |

## Stack
- Python 3.x
- Tesseract OCR
- pdfplumber
- pytesseract
- rapidfuzz
- FastAPI (REST endpoint)

## Quick start
```bash
pip install -r requirements.txt
python -m src.main path/to/document.pdf --config src/field_config.yaml
```

## API
```bash
uvicorn src.api:app --host 0.0.0.0 --port 8000
```
`POST /extract  multipart/form-data  file=<pdf>`

## Architecture
```
input PDF
  → page rendering (300 DPI)
  → pre-pass section scanner (page_section_map)
  → Tesseract OCR → token grid
  → spatial clustering → TableRows
  → fuzzy keyword matching (rapidfuzz, score ≥ 82)
  → year-column detection → cell selection
  → math repair engine (authentic values only)
  → structured JSON output
```

## Adding new fields
Edit `src/field_config.yaml`. No code changes required.

## Extending to new jurisdictions
Add keywords for new accounting terminology to `field_config.yaml`.
See `artifacts/generalization_test_*.md` for per-jurisdiction coverage.
