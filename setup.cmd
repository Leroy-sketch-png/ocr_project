@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON_CMD=python"

if not exist "%ROOT%.venv" (
    %PYTHON_CMD% -m venv "%ROOT%.venv"
)

"%ROOT%.venv\Scripts\python.exe" -m pip install --upgrade pip
"%ROOT%.venv\Scripts\pip.exe" install -r "%ROOT%requirements.txt"

echo.
echo Python dependencies are ready.
echo Ensure Tesseract OCR is installed and available on PATH, or set TESSERACT_CMD.
echo You can verify the environment with:
echo   "%ROOT%.venv\Scripts\python.exe" -m src.main --check-env
echo Or process a document with:
echo   "%ROOT%run.cmd" -Path ..\AA_SAMPLE1.pdf -Optimize -Output .\artifacts\sample1_output.json
