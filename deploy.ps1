<#
 One-command setup for TaskPilot AI on Windows 11.

   .\deploy.ps1                   install everything, build the dashboard, start it, open the browser
   .\deploy.ps1 -InstallStartup   same, and also start TaskPilot automatically when you sign in
   .\deploy.ps1 -RemoveStartup    remove the sign-in task
   .\deploy.ps1 -Stop             stop the running TaskPilot backend

 Safe to run again at any time (it updates dependencies and rebuilds).
 Nothing here needs Administrator rights. The API only listens on 127.0.0.1.
#>
param(
  [switch]$InstallStartup, [switch]$RemoveStartup, [switch]$Stop,
  [switch]$NoBrowser, [int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$TaskName = 'TaskPilotAI'

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
}

# Stops only a TaskPilot backend (never another program that happens to use the port).
function Stop-TaskPilot {
  $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($l in $listeners) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($l.OwningProcess)" -ErrorAction SilentlyContinue
    if ($p -and $p.CommandLine -match 'uvicorn' -and $p.CommandLine -match 'main:app') {
      Stop-Process -Id $l.OwningProcess -Force -ErrorAction SilentlyContinue
    }
  }
}

if ($RemoveStartup) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host 'Startup task removed.'
}
if ($Stop -or $RemoveStartup) {
  if ($Stop) { Stop-TaskPilot; Write-Host 'TaskPilot stopped.' }
  return
}

function Get-Python {
  foreach ($name in 'py', 'python') {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    $pre = @(); if ($name -eq 'py') { $pre = @('-3') }
    try {
      $out = (& $cmd.Source @pre --version 2>&1 | Out-String)
      if ($out -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 11) { return @{ Exe = $cmd.Source; Pre = $pre } }
    } catch { continue }
  }
  return $null
}
function Get-NodeOk {
  $n = Get-Command node -ErrorAction SilentlyContinue
  if (-not $n) { return $false }
  try { return ((& node --version) -match 'v(\d+)') -and ([int]$Matches[1] -ge 18) } catch { return $false }
}

Step 'Checking Python 3.11+'
$py = Get-Python
if (-not $py) {
  Write-Host 'Python 3.11+ not found. Installing it with winget...'
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  Refresh-Path; $py = Get-Python
  if (-not $py) { throw 'Python was installed but is not on PATH yet. Close this window, open a new PowerShell and run deploy.ps1 again.' }
}
Write-Host "Using $($py.Exe)"

Step 'Checking Node.js 18+'
if (-not (Get-NodeOk)) {
  Write-Host 'Node.js not found. Installing the LTS version with winget...'
  winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
  Refresh-Path
  if (-not (Get-NodeOk)) { throw 'Node.js was installed but is not on PATH yet. Close this window, open a new PowerShell and run deploy.ps1 again.' }
}

Step 'Setting up the backend (Python virtual environment + packages)'
$venv = Join-Path $backend '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
  & $py.Exe @($py.Pre) -m venv $venv
  if ($LASTEXITCODE) { throw 'Could not create the Python virtual environment.' }
}
& $venvPy -m pip install --disable-pip-version-check -r (Join-Path $backend 'requirements.txt')
if ($LASTEXITCODE) { throw 'pip install failed. Check your internet connection and try again.' }

Step 'Building the dashboard (React)'
Push-Location $frontend
try {
  npm.cmd install --no-audit --no-fund
  if ($LASTEXITCODE) { throw 'npm install failed.' }
  npm.cmd run build
  if ($LASTEXITCODE) { throw 'The dashboard build failed. Scroll up for the error.' }
} finally { Pop-Location }

Step 'Starting TaskPilot'
Stop-TaskPilot
$runArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Join-Path $root 'run.ps1')`" -Port $Port"
if ($InstallStartup) {
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $runArgs
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Starts the TaskPilot AI local monitoring agent at sign-in.' -Force | Out-Null
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "Registered the '$TaskName' sign-in task."
} else {
  Start-Process powershell.exe -WindowStyle Minimized -ArgumentList $runArgs
}

$ready = $false
for ($i = 0; $i -lt 40 -and -not $ready; $i++) {
  try { if ((Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 2).status -eq 'ok') { $ready = $true; break } } catch {}
  Start-Sleep -Seconds 1
}
if (-not $ready) { throw "TaskPilot did not answer on port $Port. Read backend\taskpilot.log for the reason." }

Write-Host "`nTaskPilot AI is running at http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host 'Stop it:  .\deploy.ps1 -Stop      Auto-start at sign-in:  .\deploy.ps1 -InstallStartup'
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
