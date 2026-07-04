param()

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RootDir ".venv\Scripts\python.exe"
$UiEntry = Join-Path $RootDir "ui_main.py"

if (-not (Test-Path $Python)) {
    Write-Host "Virtual environment is missing. Run deploy.ps1 first." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $UiEntry)) {
    Write-Host "ui_main.py is missing. Re-run deploy.ps1 to repair the install." -ForegroundColor Red
    exit 1
}

Set-Location $RootDir
& $Python $UiEntry
