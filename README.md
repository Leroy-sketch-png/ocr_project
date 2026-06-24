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

## Bootstrap

1. **Install Tesseract OCR**
   - Install Tesseract system-wide.
   - Make sure the executable is available on `PATH`, or set `TESSERACT_CMD` to the full executable path.

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Optional PaddleOCR engine**
   - The code supports `--engine paddle` for alternate OCR mode.
   - That engine requires `paddleocr` to be installed separately.

3. **Optional environment variable**
   ```powershell
   $env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
   ```

## Execution

```bash
python -m src.main path/to/document.pdf
```

Useful flags:

- `--engine tesseract` uses the default offline path.
- `--engine paddle` uses PaddleOCR if installed.
- `--optimize` enables the math repair pass.
- `--extreme` is a compatibility alias for `--optimize`.
- `--check-env` prints an environment readiness report.

Example:

```bash
python -m src.main AA_SAMPLE1.pdf --optimize
```

Environment check:

```bash
python -m src.main --check-env
```

## Runtime Notes

- Tesseract is resolved in this order: `TESSERACT_CMD`, `tesseract` on `PATH`, then common Windows install locations such as `%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe`.
- If Tesseract or PaddleOCR is missing, the program returns a clean JSON error with a remediation hint instead of a traceback.
- `--check-env` confirms local prerequisites before execution.
- The repair engine applies accounting identities such as `Total Assets = Total Liabilities + Total Equity`.
- When a field fails validation, the pipeline can re-check alternate cell candidates and re-OCR suspicious regions to recover from common OCR mistakes.

## Bootstrap Script

To install the Python dependencies in one step, run:

```powershell
.\setup.ps1
```

Then verify the OCR prerequisites:

```powershell
python -m src.main --check-env
```

## Run Script

To process a document with the bundled PowerShell wrapper:

```powershell
.\run.ps1 -Path .\AA_SAMPLE1.pdf -Optimize
```

## Running Tests & OCD Protocol Validation

```bash
pytest tests/
black src tests --check
flake8 src tests
mypy src
```
