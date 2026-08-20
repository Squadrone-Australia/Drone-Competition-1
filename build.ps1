<#
.SYNOPSIS
    Build the shipping Windows installer: tests -> PyInstaller -> Inno Setup.

    Produces dist\comp1-Setup-<version>.exe and dist\SHA256SUMS.txt. Both files
    go up as GitHub release assets: the in-app updater reads SHA256SUMS.txt and
    refuses any installer whose digest does not match, so a release without it
    is a release nobody can auto-update to.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -SkipTests        # iterating on packaging, not on code
#>
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvDir = "venv"
if (-not (Test-Path "venv\Scripts\python.exe") -and (Test-Path ".venv\Scripts\python.exe")) {
    $venvDir = ".venv"
}
$venvPython = "$venvDir\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "No venv found. Run .\start.ps1 once, or: python -m venv venv; venv\Scripts\pip install -e '.[dev]'"
    exit 1
}

# One version, read from the one place it lives.
$versionLine = Select-String -Path "comp1\__init__.py" -Pattern '__version__ = "([^"]+)"'
$version = $versionLine.Matches[0].Groups[1].Value
Write-Host "==> Building Squadrone Drone Coder $version" -ForegroundColor Cyan

if (-not $SkipTests) {
    Write-Host "==> Running the test suite..." -ForegroundColor Cyan
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Tests failed - not building an installer from this tree."
        exit 1
    }
}

Write-Host "==> PyInstaller (onedir)..." -ForegroundColor Cyan
& $venvPython -m PyInstaller comp1.spec --noconfirm
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed."; exit 1 }

# Inno Setup is a separate download (jrsoftware.org/isdl.php) and is not on PATH
# by default, so look where it actually installs before giving up. Newest major
# first: the script targets 6+ syntax and every later version still compiles it.
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $candidates = @()
    foreach ($major in 7, 6) {
        $candidates += "$env:ProgramFiles\Inno Setup $major\ISCC.exe"
        $candidates += "${env:ProgramFiles(x86)}\Inno Setup $major\ISCC.exe"
    }
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    Write-Warning "Inno Setup not found - stopping after the portable build."
    Write-Warning "dist\comp1\comp1.exe is ready to run. For the installer, get Inno Setup 6+"
    Write-Warning "from https://jrsoftware.org/isdl.php and run this script again."
    exit 0
}

Write-Host "==> Inno Setup..." -ForegroundColor Cyan
& $iscc "/DAppVersion=$version" "installer\comp1.iss"
if ($LASTEXITCODE -ne 0) { Write-Error "Inno Setup failed."; exit 1 }

$setup = "dist\comp1-Setup-$version.exe"
if (-not (Test-Path $setup)) { Write-Error "Expected $setup, but it is not there."; exit 1 }

# The updater looks the installer up in this file *by name*, so the format is
# the plain "<hash>  <filename>" one, with no path in front of the name.
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
$name = Split-Path $setup -Leaf
"$hash  $name" | Out-File -FilePath "dist\SHA256SUMS.txt" -Encoding ascii

Write-Host ""
Write-Host "Built $setup" -ForegroundColor Green
Write-Host "SHA-256 $hash" -ForegroundColor Green
Write-Host ""
Write-Host "Publish BOTH files as assets on a GitHub release tagged v$version," -ForegroundColor Yellow
Write-Host "or installed copies will not see the update." -ForegroundColor Yellow
