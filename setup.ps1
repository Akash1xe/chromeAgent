$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host ""
Write-Host "Chrome Agent Windows Setup"
Write-Host "=========================="

if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ was not found. Install Python and ensure it is available in PATH."
}

if (-not (Test-Path ".venv")) {
    Write-Host "[1/5] Creating Python virtual environment..."
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -m venv .venv
    } else {
        & python -m venv .venv
    }
} else {
    Write-Host "[1/5] Python virtual environment already exists."
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Virtual environment Python was not created correctly."
}

Write-Host "[2/5] Installing backend dependencies..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r "backend\requirements.txt"

Write-Host "[3/5] Installing Playwright Chromium..."
& $Python -m playwright install chromium

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm was not found. Install Node.js 20+ and rerun setup.ps1."
}

Write-Host "[4/5] Installing frontend dependencies..."
Push-Location "frontend"
try {
    & npm install --no-audit --no-fund
} finally {
    Pop-Location
}

Write-Host "[5/5] Preparing environment file..."
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example."
} else {
    Write-Host ".env already exists; leaving it unchanged."
}

Write-Host ""
Write-Host "Running environment diagnostics..."
Push-Location "backend"
try {
    & $Python doctor.py
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Add your Groq/Gemini keys to .env if desired."
Write-Host "Then start everything with:"
Write-Host "  .\.venv\Scripts\python.exe run.py"
