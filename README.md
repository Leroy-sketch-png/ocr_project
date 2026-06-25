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
   ```bat
   .\setup.cmd
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
   ```bat
   .\run.cmd ..\AA_SAMPLE1.pdf --optimize --output .\artifacts\sample1_output.json
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

## Evaluation Report

To regenerate the saved evaluation summary and comparison report:

```bat
python .\tools\generate_evaluation_report.py
```

This writes:

- [artifacts/evaluation_report.json](artifacts/evaluation_report.json)
- [artifacts/evaluation_report.md](artifacts/evaluation_report.md)

## Design Decisions

- The pipeline is split into small modules so each stage can be tested independently.
- OCR output is preserved with page number and evidence so every extracted value remains traceable.
- Validation happens after extraction so partially correct runs still return structured results.
- The repair engine is constraint-driven and only adjusts values when accounting identities support the change.
- The CLI can print to stdout and write a JSON artifact so the same run supports both inspection and submission.

## Assignment Coverage

- Task 1, file input: [src/io_loader.py](src/io_loader.py)
- Task 2, OCR extraction: [src/ocr_engine.py](src/ocr_engine.py)
- Task 3, table reconstruction: [src/table_builder.py](src/table_builder.py)
- Task 4, field extraction: [src/field_extractor.py](src/field_extractor.py) and [src/field_config.yaml](src/field_config.yaml)
- Task 5, value parsing: [src/value_parser.py](src/value_parser.py)
- Task 6, validation: [src/validator.py](src/validator.py)
- Task 7, OCR failure detection: [src/validator.py](src/validator.py)
- Task 8, structured output with evidence: [src/exporter.py](src/exporter.py) and [artifacts/sample1_output.json](artifacts/sample1_output.json)

## Example Output

The repository can write a submission artifact such as:

```powershell
.\run.cmd ..\AA_SAMPLE1.pdf --optimize --output .\artifacts\sample1_output.json
```

The generated JSON contains the extracted fields, evidence text, page numbers, and any validation message.
An example artifact is included at [artifacts/sample1_output.json](artifacts/sample1_output.json).

## Running Tests & Validation

```bash
pytest tests/
black src tests --check
flake8 src tests
mypy src
```
