<#
.SYNOPSIS
    One-click setup + launch for comp1.

    Creates the venv if it doesn't exist, installs/updates dependencies,
    then starts the app (simulator by default). Safe to re-run any time —
    it skips steps that are already done.

.EXAMPLE
    .\start.ps1
    .\start.ps1 --drone tello
    .\start.ps1 --drone sim --seed 42
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvPython = "venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "==> No venv found. Creating one (requires Python 3.11+ on PATH)..." -ForegroundColor Cyan
    python -m venv venv
    if (-not (Test-Path $venvPython)) {
        Write-Error "venv creation failed. Make sure Python 3.11+ is installed and on PATH."
        exit 1
    }
}

Write-Host "==> Installing/updating dependencies (pip install -e .[dev])..." -ForegroundColor Cyan
& $venvPython -m pip install -e ".[dev]" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Error "Dependency install failed."
    exit 1
}

Write-Host "==> Starting comp1..." -ForegroundColor Cyan
if ($AppArgs) {
    & $venvPython -m comp1 @AppArgs
} else {
    & $venvPython -m comp1
}
