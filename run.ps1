# Production runner: ONE process serves the API and the built dashboard on 127.0.0.1.
# Normally started by deploy.ps1. Logs go to backend\taskpilot.log.
param([int]$Port = 8000)
$backend = Join-Path $PSScriptRoot 'backend'
$py      = Join-Path $backend '.venv\Scripts\python.exe'
$log     = Join-Path $backend 'taskpilot.log'
if (-not (Test-Path $py)) { Write-Host 'Backend is not set up yet. Run .\deploy.ps1 first.'; exit 1 }
Set-Location $backend
# cmd.exe handles the log redirect so PowerShell 5.1 does not turn uvicorn output into errors.
cmd.exe /c "`"$py`" -m uvicorn main:app --host 127.0.0.1 --port $Port >> `"$log`" 2>&1"
