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

2. Install the Python dependencies.
   ```powershell
   .\setup.ps1
   ```

3. Optionally point `TESSERACT_CMD` at your local executable.
   ```powershell
   $env:TESSERACT_CMD = "<full-path-to-tesseract.exe>"
   ```

4. Verify the environment.
   ```powershell
   python -m src.main --check-env
   ```

5. Run the pipeline.
   ```powershell
   .\run.ps1 -Path .\AA_SAMPLE1.pdf -Optimize -Output .\artifacts\sample1_output.json
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
- `--output` writes the JSON response to a file.

## Runtime Notes

- Tesseract is resolved in this order: `TESSERACT_CMD`, `tesseract` on `PATH`, then common Windows install locations.
- If Tesseract or PaddleOCR is missing, the program returns a structured JSON error with a remediation hint instead of a traceback.
- `--check-env` confirms local prerequisites before execution.
- The repair engine applies accounting identities such as `Total Assets = Total Liabilities + Total Equity`.
- When a field fails validation, the pipeline can re-check alternate cell candidates and re-OCR suspicious regions to recover from common OCR mistakes.

## Optional Mode

- `--engine paddle` is available as an alternate OCR backend.
- It requires the optional `paddleocr` package.

## Design Decisions

- The pipeline is split into small modules so each stage can be tested independently.
- OCR output is preserved with page number and evidence so every extracted value remains traceable.
- Validation happens after extraction so partially correct runs still return structured results.
- The repair engine is constraint-driven and only adjusts values when accounting identities support the change.
- The CLI can print to stdout and write a JSON artifact so the same run supports both inspection and submission.

## Example Output

The repository can write a submission artifact such as:

```powershell
.\run.ps1 -Path ..\AA_SAMPLE1.pdf -Optimize -Output .\artifacts\sample1_output.json
```

The generated JSON contains the extracted fields, evidence text, page numbers, and any validation message.
An example artifact is included at [artifacts/sample1_output.json](/C:/Users/c-leroy.phan/Downloads/ai/ocr_project/artifacts/sample1_output.json).

## Running Tests & Validation

```bash
pytest tests/
black src tests --check
flake8 src tests
mypy src
```
