$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

Write-Host "=== TaskPilot AI - Build and Install ===" -ForegroundColor Cyan

Write-Host "[1/6] Installing Python dependencies..." -ForegroundColor Yellow
pip install --upgrade pystray Pillow pyinstaller -r "$ProjectRoot\backend\requirements.txt"

Write-Host "[2/6] Building frontend..." -ForegroundColor Yellow
Push-Location "$ProjectRoot\frontend"
npm install
npm run build
Pop-Location

Write-Host "[3/6] Running PyInstaller..." -ForegroundColor Yellow
Push-Location $ProjectRoot
pyinstaller taskpilot.spec --clean --noconfirm
Pop-Location

$distExe = "$ProjectRoot\dist\TaskPilotAI\TaskPilotAI.exe"
if (-not (Test-Path $distExe)) {
    Write-Error "Build failed - exe not found at $distExe"
    exit 1
}
Write-Host "Build OK: $distExe" -ForegroundColor Green

Write-Host "[4/6] Installing to C:\TaskPilotAI..." -ForegroundColor Yellow
$installDir = "C:\TaskPilotAI"
if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }
Copy-Item -Recurse "$ProjectRoot\dist\TaskPilotAI" $installDir
Write-Host "Installed to $installDir" -ForegroundColor Green

Write-Host "[5/6] Registering auto-start task..." -ForegroundColor Yellow
$xmlTemplate = "$PSScriptRoot\TaskPilotAI-scheduler.xml"
$xmlTemp = "$env:TEMP\TaskPilotAI-task.xml"
$username = $env:USERNAME
(Get-Content $xmlTemplate) -replace "YOURUSERNAME", $username | Set-Content $xmlTemp -Encoding Unicode
schtasks /Delete /TN "TaskPilot AI" /F 2>$null
schtasks /Create /XML $xmlTemp /TN "TaskPilot AI"
Write-Host "Scheduled task registered for: $username" -ForegroundColor Green

Write-Host "[6/6] Launching TaskPilot AI..." -ForegroundColor Yellow
Start-Process "$installDir\TaskPilotAI.exe"

Write-Host "Done! TaskPilot AI is running in your system tray." -ForegroundColor Green
Write-Host "Dashboard: http://127.0.0.1:8000" -ForegroundColor Green