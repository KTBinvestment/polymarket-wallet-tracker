$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$VenvDir = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating virtual environment in .venv..."
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        & py -3.12 -m venv .venv
    } else {
        & python -m venv .venv
    }
}

Write-Host "Installing dependencies..."
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r requirements.txt

Write-Host "Starting Polymarket Wallet Tracker..."
& $PythonExe -m streamlit run app.py
