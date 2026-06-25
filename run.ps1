param(
    [Parameter(Mandatory = $false)]
    [string]$Path,

    [string]$Engine = "tesseract",

    [switch]$Optimize,

    [switch]$CheckEnv,

    [string]$Output,

    [string]$Config
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $PSScriptRoot "src\field_config.yaml"
}

if ($CheckEnv) {
    if (Test-Path (Join-Path $PSScriptRoot ".venv\Scripts\python.exe")) {
        & (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") -m src.main --check-env --engine $Engine
    } else {
        & python -m src.main --check-env --engine $Engine
    }
    exit $LASTEXITCODE
}

if ([string]::IsNullOrWhiteSpace($Path)) {
    throw "Please provide -Path or use -CheckEnv."
}

$pythonExe = if (Test-Path (Join-Path $PSScriptRoot ".venv\Scripts\python.exe")) {
    Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
} else {
    "python"
}

$args = @("-m", "src.main", $Path, "--config", $Config, "--engine", $Engine)
if ($Optimize) {
    $args += "--optimize"
}
if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $args += "--output"
    $args += $Output
}

& $pythonExe @args
