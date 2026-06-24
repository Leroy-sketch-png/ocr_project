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
- `--optimize`: Enables the constraint-guided repair engine that can reconcile fields using accounting identities and targeted re-OCR.

## Quick Start

1. Install Tesseract OCR.
   - Install Tesseract system-wide.
   - Make sure the executable is available on `PATH`, or set `TESSERACT_CMD` to the full executable path.
   - Optional example:
     ```powershell
     $env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
     ```

2. Install the Python dependencies.
   ```powershell
   .\setup.ps1
   ```

3. Verify the environment.
   ```powershell
   python -m src.main --check-env
   ```

4. Run the pipeline.
   ```powershell
   .\run.ps1 -Path .\AA_SAMPLE1.pdf -Optimize
   ```

## Command Line

```bash
python -m src.main path/to/document.pdf
```

Available flags:

- `--engine tesseract` uses the default offline path.
- `--engine paddle` uses PaddleOCR if installed.
- `--optimize` enables the math repair pass.
- `--extreme` is a compatibility alias for `--optimize`.
- `--check-env` prints an environment readiness report.

## Runtime Notes

- Tesseract is resolved in this order: `TESSERACT_CMD`, `tesseract` on `PATH`, then common Windows install locations such as `%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe`.
- If Tesseract or PaddleOCR is missing, the program returns a structured JSON error with a remediation hint instead of a traceback.
- `--check-env` confirms local prerequisites before execution.
- The repair engine applies accounting identities such as `Total Assets = Total Liabilities + Total Equity`.
- When a field fails validation, the pipeline can re-check alternate cell candidates and re-OCR suspicious regions to recover from common OCR mistakes.

## Optional Mode

- `--engine paddle` is available as an alternate OCR backend.
- It requires the optional `paddleocr` package.

## Running Tests & OCD Protocol Validation

```bash
pytest tests/
black src tests --check
flake8 src tests
mypy src
```
