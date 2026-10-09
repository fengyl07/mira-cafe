$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 -m venv .venv
  } else {
    python -m venv .venv
  }
}
.\.venv\Scripts\python -m pip install -r requirements.txt
Write-Host "Open http://127.0.0.1:8000"
.\.venv\Scripts\python -m uvicorn app.main:app --reload --reload-dir app --reload-dir web --host 127.0.0.1 --port 8000
