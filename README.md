# Offline OCR Pipeline

A fully offline, OCR-based financial data extraction pipeline relying on Tesseract and pdfplumber.

## Features
- Task 1: Reads PDF and image files.
- Task 2: OCR text extraction via Tesseract.
- Task 3: Table reconstruction using y-coordinate clustering.
- Task 4: Financial field extraction based on `field_config.yaml`.
- Task 5: Value parsing supporting negatives like `(1,234)`.
- Task 6 & 7: OCR failure and field validation.
- Task 8: Outputs structured JSON with raw evidence tracking.

## Setup

1. **Install Tesseract OCR:** Ensure you have the Tesseract executable installed on your system.
2. **Install requirements:**
   ```bash
   pip install -r requirements.txt
   ```

## Usage

```bash
python -m src.main path/to/document.pdf
```

## Running Tests & OCD Protocol Validation

```bash
pytest tests/
black src tests --check
flake8 src tests
mypy src
```
