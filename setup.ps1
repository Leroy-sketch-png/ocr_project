param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    & $Python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt

Write-Host ""
Write-Host "Python dependencies are ready."
Write-Host "Next, make sure Tesseract OCR is installed and available on PATH, or set TESSERACT_CMD."
Write-Host "You can verify the environment with:"
Write-Host "  .\.venv\Scripts\python.exe -m src.main --check-env"
Write-Host "Or process a document with:"
Write-Host "  .\run.ps1 -Path .\AA_SAMPLE1.pdf -Optimize"
